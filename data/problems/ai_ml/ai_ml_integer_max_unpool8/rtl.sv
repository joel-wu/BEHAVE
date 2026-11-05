// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module MaxUnpool8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_data,
    input logic [31:0] in_indices,
    output logic [127:0] out_values
);

logic [127:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer j=0;j<16;j=j+1) begin
    value=0;
    for(integer i=0;i<8;i=i+1)
      if(in_indices[4*i +: 4]==4'(j)) value=32'(in_data[8*i +: 8]);
    result[8*j +: 8]=8'(value);
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
