// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module CompressRows8x2 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_data,
    input logic [7:0] in_mask,
    output logic [131:0] out_values
);

logic [131:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : compact
    integer count;
    count=0;
    for(integer i=0;i<8;i=i+1) begin
      if(in_mask[i]) begin
        result[16*count +: 16]=in_data[16*i +: 16];
        count=count+1;
      end
    end
    result[131:128]=4'(count);
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
