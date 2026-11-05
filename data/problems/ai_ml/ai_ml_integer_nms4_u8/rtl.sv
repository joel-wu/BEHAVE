// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module NMS4U8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_boxes,
    input logic [31:0] in_scores,
    input logic [7:0] in_threshold,
    output logic [10:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction
logic [10:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : nms
    integer count,best,best_score,score;
    integer ya,yb,xa,xb,yc,yd,xc,xd,area,inter_h,inter_w,intersection,union_area;
    logic [3:0] visited,blocked;
    logic [31:0] first,other;
    count=0; visited=0; blocked=0;
    for(integer rank=0;rank<4;rank=rank+1) begin
      best=0; best_score=-129;
      for(integer i=0;i<4;i=i+1) begin
        score=s8(in_scores[8*i +: 8]);
        if(!visited[i] && score>best_score) begin best=i; best_score=score; end
      end
      visited[best]=1;
      if(!blocked[best]) begin
        result[2*count +: 2]=2'(best); count=count+1;
        first=in_boxes[32*best +: 32];
        ya=first[7:0]<first[23:16] ? 32'(first[7:0]):32'(first[23:16]);
        yb=first[7:0]>first[23:16] ? 32'(first[7:0]):32'(first[23:16]);
        xa=first[15:8]<first[31:24] ? 32'(first[15:8]):32'(first[31:24]);
        xb=first[15:8]>first[31:24] ? 32'(first[15:8]):32'(first[31:24]);
        area=(yb-ya)*(xb-xa);
        for(integer j=0;j<4;j=j+1) begin
          other=in_boxes[32*j +: 32];
          yc=other[7:0]<other[23:16] ? 32'(other[7:0]):32'(other[23:16]);
          yd=other[7:0]>other[23:16] ? 32'(other[7:0]):32'(other[23:16]);
          xc=other[15:8]<other[31:24] ? 32'(other[15:8]):32'(other[31:24]);
          xd=other[15:8]>other[31:24] ? 32'(other[15:8]):32'(other[31:24]);
          inter_h=(yb<yd ? yb:yd)-(ya>yc ? ya:yc);
          inter_w=(xb<xd ? xb:xd)-(xa>xc ? xa:xc);
          if(inter_h<0) inter_h=0; if(inter_w<0) inter_w=0;
          intersection=inter_h*inter_w;
          union_area=area+(yd-yc)*(xd-xc)-intersection;
          if(intersection>0 && 255*intersection>32'(in_threshold)*union_area) blocked[j]=1;
        end
      end
    end
    result[10:8]=3'(count);
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
