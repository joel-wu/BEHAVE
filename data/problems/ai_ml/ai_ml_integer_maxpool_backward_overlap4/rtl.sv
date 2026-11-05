// SPDX-License-Identifier: BSD-3-Clause
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module MaxPoolBackwardOverlap4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_x,
    input logic [63:0] in_grad,
    output logic [511:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction
function automatic signed [31:0] s16(input logic [15:0] x);
  s16 = {{16{x[15]}}, x};
endfunction

logic [511:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : backward_pool
    integer dx[0:15];integer best,index,j;
    for(integer i=0;i<16;i=i+1) dx[i]=0;
    for(integer h=0;h<2;h=h+1) for(integer w=0;w<2;w=w+1) begin
      best=-129;index=0;
      for(integer kh=0;kh<3;kh=kh+1) for(integer kw=0;kw<3;kw=kw+1) begin
        j=4*(h+kh)+w+kw;value=s8(in_x[8*j +: 8]);if(value>best) begin best=value;index=j;end
      end
      dx[index]=dx[index]+s16(in_grad[16*(2*h+w) +: 16]);
    end
    for(integer i=0;i<16;i=i+1) result[32*i +: 32]=dx[i];
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
