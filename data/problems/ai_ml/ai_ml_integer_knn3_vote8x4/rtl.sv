// SPDX-License-Identifier: BSD-3-Clause
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module KNN3Vote8x4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_points,
    input logic [31:0] in_query,
    input logic [15:0] in_labels,
    input logic [7:0] in_mask,
    output logic [78:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction

logic [78:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : classify
    integer distances[0:7]; integer votes[0:3]; integer used,count,best,distance,found,label,winner,d;
    for(integer i=0;i<8;i=i+1) begin
      distances[i]=0;
      for(integer c=0;c<4;c=c+1) begin
        d=s8(in_points[8*(4*i+c) +: 8])-s8(in_query[8*c +: 8]); distances[i]=distances[i]+d*d;
      end
    end
    for(integer c=0;c<4;c=c+1) votes[c]=0;
    used=0;count=0;winner=0;
    for(integer rank=0;rank<3;rank=rank+1) begin
      best=0;distance=1048576;found=0;
      for(integer i=0;i<8;i=i+1) if(in_mask[i] && !(used & (1<<i)) && distances[i]<distance) begin
        best=i;distance=distances[i];found=1;
      end
      if(found) begin
        used=used|(1<<best);label=in_labels[2*best +: 2];votes[label]=votes[label]+1;
        result[3*rank +: 3]=best;result[25+18*rank +: 18]=distance;count=count+1;
      end
    end
    result[9 +: 2]=count;
    for(integer c=0;c<4;c=c+1) begin result[11+3*c +: 3]=votes[c];if(votes[c]>votes[winner]) winner=c;end
    result[23 +: 2]=winner;
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
