// SPDX-License-Identifier: MIT
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module BitmapRankSelect128 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_bitmap,
    input logic [6:0] in_position,
    input logic [7:0] in_ordinal,
    output logic [23:0] out_values
);


logic [23:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : rank_select
    integer rank_v,count_v,selected,valid_v;
    rank_v=0; count_v=0; selected=0; valid_v=0;
    for(integer i=0;i<128;i=i+1)
      if(in_bitmap[i]) begin
        if(i<=in_position) rank_v=rank_v+1;
        if(count_v==in_ordinal) begin selected=i; valid_v=1; end
        count_v=count_v+1;
      end
    result={valid_v[0],selected[6:0],count_v[7:0],rank_v[7:0]};
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
