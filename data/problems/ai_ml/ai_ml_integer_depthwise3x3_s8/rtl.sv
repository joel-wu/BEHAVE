// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module Depthwise3x3S8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_data,
    input logic [143:0] in_weights,
    input logic [31:0] in_bias,
    output logic [255:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction
function automatic signed [31:0] s16(input logic [15:0] x);
  s16 = {{16{x[15]}}, x};
endfunction
logic [255:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for (integer h=0; h<2; h=h+1)
    for (integer w=0; w<2; w=w+1)
      for (integer c=0; c<2; c=c+1) begin
        value=s16(in_bias[16*c +: 16]);
        for (integer i=0; i<3; i=i+1)
          for (integer j=0; j<3; j=j+1)
            value=value+s8(in_data[8*((4*(h+i)+w+j)*2+c) +: 8])
                            *s8(in_weights[8*((3*i+j)*2+c) +: 8]);
        result[32*((2*h+w)*2+c) +: 32]=32'(value);
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
