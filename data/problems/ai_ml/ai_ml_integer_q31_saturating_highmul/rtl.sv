// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module Q31SaturatingHighMul (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_a,
    input logic [127:0] in_b,
    output logic [127:0] out_values
);
function automatic signed [31:0] highmul(input logic signed [31:0] a, b);
  logic signed [63:0] product, adjusted;
  begin
    product = 64'(a) * 64'(b);
    adjusted = product + (product >= 0 ? 64'sd1073741824 : -64'sd1073741823);
    highmul = (a == -32'sd2147483648 && b == -32'sd2147483648)
              ? 32'sd2147483647 : 32'(adjusted / 64'sd2147483648);
  end
endfunction
logic [127:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer i=0;i<4;i=i+1)
    result[32*i +: 32]=highmul($signed(in_a[32*i +: 32]),$signed(in_b[32*i +: 32]));
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
