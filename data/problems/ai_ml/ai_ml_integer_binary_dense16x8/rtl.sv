// SPDX-License-Identifier: BSD-3-Clause
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module BinaryDense16x8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [15:0] in_data,
    input logic [127:0] in_weights,
    input logic [63:0] in_thresholds,
    output logic [71:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction

logic [71:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer o=0;o<8;o=o+1) begin
    value=0;
    for(integer k=0;k<16;k=k+1)
      value=value+((in_data[k] ? 1:-1)*(in_weights[16*o+k] ? 1:-1));
    result[8*o +: 8]=value; result[64+o]=(value>=s8(in_thresholds[8*o +: 8]));
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
