// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module GroupwiseW4MatVec16x4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_data,
    input logic [255:0] in_weights,
    input logic [7:0] in_zero_x,
    input logic [63:0] in_zero_w,
    input logic [63:0] in_scales,
    input logic [63:0] in_bias,
    output logic [127:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction
function automatic signed [31:0] s16(input logic [15:0] x);
  s16 = {{16{x[15]}}, x};
endfunction
function automatic signed [31:0] s4(input logic [3:0] x);
  s4={{28{x[3]}},x};
endfunction

logic [127:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer o=0;o<4;o=o+1) begin
    integer total,zero,scale;
    total=s16(in_bias[16*o +: 16]);
    for(integer g=0;g<4;g=g+1) begin
      value=0; zero=s4(in_zero_w[4*(4*o+g) +: 4]); scale=1+in_scales[4*(4*o+g) +: 4];
      for(integer k=0;k<4;k=k+1)
        value=value+(s8(in_data[8*(4*g+k) +: 8])-s8(in_zero_x))
                    *(s4(in_weights[4*(16*o+4*g+k) +: 4])-zero);
      total=total+value*scale;
    end
    result[32*o +: 32]=total;
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
