// SPDX-License-Identifier: BSD-3-Clause
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module ConvWeightBackward2x4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_x,
    input logic [63:0] in_grad,
    output logic [1183:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction

logic [1183:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer o=0;o<2;o=o+1) begin
    for(integer c=0;c<2;c=c+1) for(integer kh=0;kh<3;kh=kh+1) for(integer kw=0;kw<3;kw=kw+1) begin
      value=0;
      for(integer h=0;h<2;h=h+1) for(integer w=0;w<2;w=w+1)
        value=value+s8(in_grad[8*(4*o+2*h+w) +: 8])*s8(in_x[8*(16*c+4*(h+kh)+w+kw) +: 8]);
      result[32*(18*o+9*c+3*kh+kw) +: 32]=value;
    end
    value=0;for(integer j=0;j<4;j=j+1) value=value+s8(in_grad[8*(4*o+j) +: 8]);
    result[1152+16*o +: 16]=value;
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
