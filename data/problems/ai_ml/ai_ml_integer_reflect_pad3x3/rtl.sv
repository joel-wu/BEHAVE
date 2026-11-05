// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module ReflectPad3x3 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [71:0] in_data,
    output logic [199:0] out_values
);

logic [199:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer h=0;h<5;h=h+1) begin
    for(integer w=0;w<5;w=w+1) begin
      integer ih,iw;
      ih=(h==0 || h==4) ? 1 : h-1;
      iw=(w==0 || w==4) ? 1 : w-1;
      result[8*(5*h+w) +: 8]=in_data[8*(3*ih+iw) +: 8];
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
