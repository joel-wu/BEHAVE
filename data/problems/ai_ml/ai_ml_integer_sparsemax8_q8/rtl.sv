// SPDX-License-Identifier: MIT
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module Sparsemax8Q8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_data,
    output logic [143:0] out_values
);
function automatic signed [31:0] s16(input logic [15:0] x);
  s16 = {{16{x[15]}}, x};
endfunction
function automatic signed [31:0] even_ratio(input logic signed [63:0] n,d);
  logic signed [63:0] a,q,r;
  begin
    a=n<0 ? -n:n; q=a/d; r=a-q*d;
    if(2*r>d || (2*r==d && q[0])) q=q+1;
    even_ratio=n<0 ? -q:q;
  end
endfunction

logic [143:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : projection
    integer z[0:7]; integer running,count,total,temp,numerator;
    for(integer i=0;i<8;i=i+1) z[i]=s16(in_data[16*i +: 16]);
    for(integer i=0;i<8;i=i+1)
      for(integer j=0;j<7;j=j+1) if(z[j]<z[j+1]) begin temp=z[j]; z[j]=z[j+1]; z[j+1]=temp; end
    running=0; count=1; total=z[0];
    for(integer i=0;i<8;i=i+1) begin
      running=running+z[i]; if(256+(i+1)*z[i]>running) begin count=i+1; total=running; end
    end
    for(integer i=0;i<8;i=i+1) begin
      numerator=count*s16(in_data[16*i +: 16])-total+256;
      if(numerator>0) begin result[17*i +: 17]=even_ratio(256*numerator,count); result[136+i]=1; end
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
