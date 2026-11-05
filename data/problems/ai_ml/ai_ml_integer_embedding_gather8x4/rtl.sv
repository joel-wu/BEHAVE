// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module EmbeddingGather8x4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_table,
    input logic [11:0] in_indices,
    output logic [127:0] out_values
);

logic [127:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer i=0;i<4;i=i+1)
    for(integer c=0;c<4;c=c+1)
      result[8*(4*i+c) +: 8]=in_table[8*(4*in_indices[3*i +: 3]+c) +: 8];
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
