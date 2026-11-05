// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module QLinearConv3x3PerChannel (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_data,
    input logic [431:0] in_weights,
    input logic [47:0] in_bias,
    input logic [7:0] in_zero_x,
    input logic [23:0] in_zero_w,
    input logic [11:0] in_shifts,
    output logic [95:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction
function automatic signed [31:0] s16(input logic [15:0] x);
  s16 = {{16{x[15]}}, x};
endfunction
function automatic signed [31:0] clip8(input logic signed [63:0] x);
  clip8 = x < -128 ? -128 : x > 127 ? 127 : 32'(x);
endfunction
function automatic signed [31:0] rne(input logic signed [63:0] x, input integer shift);
  logic signed [63:0] d,q,r;
  begin
    d=64'sd1 << shift; q=x >>> shift; r=x-q*d;
    rne=q+((2*r>d || (2*r==d && q[0])) ? 1:0);
  end
endfunction

logic [95:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer o=0;o<3;o=o+1)
    for(integer h=0;h<2;h=h+1)
      for(integer w=0;w<2;w=w+1) begin
        value=s16(in_bias[16*o +: 16]);
        for(integer c=0;c<2;c=c+1)
          for(integer i=0;i<3;i=i+1)
            for(integer j=0;j<3;j=j+1)
              value=value+(s8(in_data[8*(16*c+4*(h+i)+w+j) +: 8])-s8(in_zero_x))
                  *(s8(in_weights[8*(18*o+9*c+3*i+j) +: 8])-s8(in_zero_w[8*o +: 8]));
        value=clip8(rne(value,in_shifts[4*o +: 4])); result[8*(4*o+2*h+w) +: 8]=value;
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
