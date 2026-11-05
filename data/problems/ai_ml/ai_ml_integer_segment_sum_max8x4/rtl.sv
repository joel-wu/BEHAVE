// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module SegmentSumMax8x4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_data,
    input logic [15:0] in_segments,
    input logic [7:0] in_mask,
    output logic [399:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction

logic [399:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer s=0;s<4;s=s+1) begin
    integer count;
    count=0;
    for(integer i=0;i<8;i=i+1) if(in_mask[i] && in_segments[2*i +: 2]==s) count=count+1;
    result[100*s+96 +: 4]=count;
    for(integer c=0;c<4;c=c+1) begin
      integer peak;
      value=0; peak=-128;
      for(integer i=0;i<8;i=i+1) if(in_mask[i] && in_segments[2*i +: 2]==s) begin
        sample=s8(in_data[8*(4*i+c) +: 8]); value=value+sample; if(sample>peak) peak=sample;
      end
      if(count==0) peak=0;
      result[100*s+16*c +: 16]=value; result[100*s+64+8*c +: 8]=peak;
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
