// SPDX-License-Identifier: BSD-3-Clause
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module DenseBackward2x4x3 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_data,
    input logic [95:0] in_weights,
    input logic [47:0] in_gradient,
    output logic [687:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction

logic [687:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer b=0;b<2;b=b+1)
    for(integer c=0;c<4;c=c+1) begin
      value=0;for(integer o=0;o<3;o=o+1) value=value+s8(in_gradient[8*(3*b+o) +: 8])*s8(in_weights[8*(4*o+c) +: 8]);
      result[32*(4*b+c) +: 32]=value;
    end
  for(integer o=0;o<3;o=o+1) begin
    for(integer c=0;c<4;c=c+1) begin
      value=0;for(integer b=0;b<2;b=b+1) value=value+s8(in_gradient[8*(3*b+o) +: 8])*s8(in_data[8*(4*b+c) +: 8]);
      result[256+32*(4*o+c) +: 32]=value;
    end
    result[640+16*o +: 16]=s8(in_gradient[8*o +: 8])+s8(in_gradient[8*(3+o) +: 8]);
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
