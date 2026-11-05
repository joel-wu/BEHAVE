// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module ReverseSequence4x8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [511:0] in_data,
    input logic [15:0] in_lengths,
    output logic [511:0] out_values
);


logic [511:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer b=0;b<4;b=b+1) begin
    integer n,src;
    n=in_lengths[4*b +: 4]; if(n>8) n=8;
    for(integer t=0;t<8;t=t+1) begin
      src=t<n ? n-1-t:t;
      result[16*(8*b+t) +: 16]=in_data[16*(8*b+src) +: 16];
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
