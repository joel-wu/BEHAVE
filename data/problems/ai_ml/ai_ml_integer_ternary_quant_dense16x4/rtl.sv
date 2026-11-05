// SPDX-License-Identifier: MIT
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module TernaryQuantDense16x4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_x,
    input logic [511:0] in_weights,
    output logic [287:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction

logic [287:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : ternary
    integer scale,w,magnitude,q;
    for(integer o=0;o<4;o=o+1) begin
      scale=0;
      for(integer c=0;c<16;c=c+1) begin
        w=s8(in_weights[8*(16*o+c) +: 8]);magnitude=w<0 ? -w:w;if(magnitude>scale) scale=magnitude;
      end
      value=0;
      for(integer c=0;c<16;c=c+1) begin
        w=s8(in_weights[8*(16*o+c) +: 8]);magnitude=w<0 ? -w:w;q=0;
        if(scale>0 && 2*magnitude>=scale) q=w<0 ? -1:1;
        value=value+q*s8(in_x[8*c +: 8]);result[160+2*(16*o+c) +: 2]=q+1;
      end
      result[32*o +: 32]=value*scale;result[128+8*o +: 8]=scale;
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
