// SPDX-License-Identifier: MIT
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module LIFDelayedBank4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_current,
    input logic [31:0] in_beta,
    input logic [63:0] in_threshold,
    input logic [0:0] in_clear,
    output logic [67:0] out_values
);
function automatic signed [31:0] s16(input logic [15:0] x);
  s16 = {{16{x[15]}}, x};
endfunction
function automatic signed [31:0] away(input logic signed [63:0] n, input integer d);
  logic signed [63:0] q;
  begin
    q=n<0 ? -((-n+d/2)/d) : (n+d/2)/d;
    away=q[31:0];
  end
endfunction
logic [63:0] membrane;
logic [67:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer i=0;i<4;i=i+1) begin
    integer old,threshold;
    old=in_clear ? 0:s16(membrane[16*i +: 16]);
    threshold=in_threshold[16*i +: 16]; if(threshold==0) threshold=1;
    value=away($signed({1'b0,in_beta[8*i +: 8]})*old,256)+s16(in_current[16*i +: 16]);
    if(old>threshold) value=value-threshold;
    if(value < -32768) value=-32768;
    if(value > 32767) value=32767;
    result[16*i +: 16]=value[15:0]; result[64+i]=value>threshold;
  end
end
assign in_ready = !out_valid || out_ready;
always_ff @(posedge clk) begin
  if (rst) begin
    out_valid <= 0;
    out_values <= 0;
    membrane <= 0;
  end else if (in_ready) begin
    out_valid <= in_valid;
    if (in_valid) out_values <= result;
    if (in_valid) membrane <= result[63:0];
  end
end
endmodule
