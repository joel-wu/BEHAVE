// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module MatMulU8ZeroPoints (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_a,
    input logic [191:0] in_b,
    input logic [7:0] in_a_zero,
    input logic [7:0] in_b_zero,
    output logic [191:0] out_values
);

logic [191:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer i=0;i<2;i=i+1) begin
    for(integer j=0;j<3;j=j+1) begin
      value=0;
      for(integer k=0;k<8;k=k+1) begin
        integer a,b;
        a=32'(in_a[8*(8*i+k) +: 8])-32'(in_a_zero);
        b=32'(in_b[8*(3*k+j) +: 8])-32'(in_b_zero);
        value=value+a*b;
      end
      result[32*(3*i+j) +: 32]=32'(value);
    end
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
