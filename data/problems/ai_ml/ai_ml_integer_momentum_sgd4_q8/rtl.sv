// SPDX-License-Identifier: BSD-3-Clause
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module MomentumSGD4Q8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_params,
    input logic [63:0] in_gradient,
    input logic [63:0] in_momentum,
    input logic [0:0] in_first,
    input logic [0:0] in_nesterov,
    output logic [127:0] out_values
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
function automatic signed [31:0] clip16(input logic signed [63:0] x);
  clip16=x< -32768 ? -32768:x>32767 ? 32767:x;
endfunction

logic [127:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer i=0;i<4;i=i+1) begin
    integer p,g,m,mnew,delta;
    p=s16(in_params[16*i +: 16]);g=s16(in_gradient[16*i +: 16]);m=s16(in_momentum[16*i +: 16]);
    mnew=in_first ? g:clip16(even_ratio(3*m+4*g,4));
    delta=in_nesterov ? even_ratio(4*g+3*mnew,64):even_ratio(mnew,16);
    result[16*i +: 16]=clip16(p-delta);result[64+16*i +: 16]=mnew;
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
