// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module LoRARank2Linear4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [31:0] in_x,
    input logic [127:0] in_w,
    input logic [63:0] in_a,
    input logic [63:0] in_b,
    output logic [127:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction

logic [127:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : low_rank
    integer hidden[0:1];
    for(integer r=0;r<2;r=r+1) begin
      hidden[r]=0;for(integer c=0;c<4;c=c+1) hidden[r]=hidden[r]+s8(in_a[8*(4*r+c) +: 8])*s8(in_x[8*c +: 8]);
    end
    for(integer o=0;o<4;o=o+1) begin
      value=0;for(integer c=0;c<4;c=c+1) value=value+s8(in_w[8*(4*o+c) +: 8])*s8(in_x[8*c +: 8]);
      for(integer r=0;r<2;r=r+1) value=value+s8(in_b[8*(2*o+r) +: 8])*hidden[r];
      result[32*o +: 32]=value;
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
