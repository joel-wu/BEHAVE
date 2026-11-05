// SPDX-License-Identifier: BSD-3-Clause
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module HistogramGiniSplit8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_counts,
    output logic [83:0] out_values
);


logic [83:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : split
    integer total0,total1,left0,left1,right0,right1,l,r,valid;
    logic [63:0] num,den,best_num,best_den;
    total0=0;total1=0;left0=0;left1=0;valid=0;best_num=0;best_den=1;
    for(integer b=0;b<8;b=b+1) begin total0=total0+in_counts[8*b +: 4];total1=total1+in_counts[8*b+4 +: 4];end
    for(integer t=0;t<7;t=t+1) begin
      left0=left0+in_counts[8*t +: 4];left1=left1+in_counts[8*t+4 +: 4];
      right0=total0-left0;right1=total1-left1;l=left0+left1;r=right0+right1;
      if(l>0 && r>0) begin
        den=(l+r)*l*r;num=den-(left0*left0+left1*left1)*r-(right0*right0+right1*right1)*l;
        if(!valid || num*best_den<best_num*den) begin
          valid=1;best_num=num;best_den=den;result[2:0]=t;result[3]=1;
          result[4 +: 8]=left0;result[12 +: 8]=left1;result[20 +: 8]=right0;result[28 +: 8]=right1;
          result[36 +: 24]=num;result[60 +: 24]=den;
        end
      end
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
