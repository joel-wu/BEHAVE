// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module RoundingShiftS32 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_data,
    input logic [4:0] in_shift,
    output logic [255:0] out_values
);
function automatic signed [31:0] rdiv(input logic signed [31:0] x, input integer exponent);
  logic [63:0] mask, remainder, threshold;
  begin
    mask = (64'd1 << exponent) - 1;
    remainder = {32'd0, x} & mask;
    threshold = (mask >> 1) + (x < 0 ? 1 : 0);
    rdiv = (x >>> exponent) + (remainder > threshold ? 1 : 0);
  end
endfunction
logic [255:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer i=0;i<8;i=i+1)
    result[32*i +: 32]=rdiv($signed(in_data[32*i +: 32]),32'(in_shift));
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
