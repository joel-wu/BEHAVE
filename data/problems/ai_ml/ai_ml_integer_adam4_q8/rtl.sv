// SPDX-License-Identifier: BSD-3-Clause
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module Adam4Q8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_params,
    input logic [63:0] in_gradient,
    input logic [63:0] in_mean,
    input logic [127:0] in_square,
    input logic [2:0] in_step_minus1,
    output logic [255:0] out_values
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
function automatic signed [31:0] root34(input logic [63:0] x);
  logic [63:0] r,candidate;
  begin
    r=0;
    for(integer k=16;k>=0;k=k-1) begin
      candidate=r+(64'd1<<k); if(candidate*candidate<=x) r=candidate;
    end
    root34=r;
  end
endfunction

logic [255:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : adam
    integer a,b;
    a=1<<(in_step_minus1+1);b=a-1;
    for(integer i=0;i<4;i=i+1) begin
      integer p,g,m,mnew,d,delta;logic [63:0] vnew,vhat;
      p=s16(in_params[16*i +: 16]);g=s16(in_gradient[16*i +: 16]);m=s16(in_mean[16*i +: 16]);
      mnew=even_ratio(m+g,2);vnew=({32'd0,in_square[32*i +: 32]}+64'(g)*64'(g))/2;
      vhat=vnew*a/b;d=root34(vhat)+1;delta=even_ratio(16*mnew*a,b*d);
      result[16*i +: 16]=clip16(p-delta);result[64+16*i +: 16]=mnew;result[128+32*i +: 32]=vnew;
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
