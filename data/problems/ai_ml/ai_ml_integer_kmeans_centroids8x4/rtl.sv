// SPDX-License-Identifier: BSD-3-Clause
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module KMeansCentroids8x4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_points,
    input logic [15:0] in_labels,
    input logic [7:0] in_mask,
    input logic [255:0] in_previous,
    output logic [271:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction
function automatic signed [31:0] s16(input logic [15:0] x);
  s16 = {{16{x[15]}}, x};
endfunction
function automatic signed [31:0] away_ratio(input logic signed [63:0] n,d);
  logic signed [63:0] a,q;
  begin a=n<0 ? -n:n; q=(2*a+d)/(2*d); away_ratio=n<0 ? -q:q; end
endfunction

logic [271:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer cluster=0;cluster<4;cluster=cluster+1) begin
    integer count,total;
    count=0;
    for(integer i=0;i<8;i=i+1) if(in_mask[i] && in_labels[2*i +: 2]==cluster) count=count+1;
    result[256+4*cluster +: 4]=count;
    for(integer c=0;c<4;c=c+1) begin
      total=0;
      for(integer i=0;i<8;i=i+1) if(in_mask[i] && in_labels[2*i +: 2]==cluster) total=total+s8(in_points[8*(4*i+c) +: 8]);
      if(count!=0) value=away_ratio(total,count);else value=s16(in_previous[16*(4*cluster+c) +: 16]);
      result[16*(4*cluster+c) +: 16]=value;
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
