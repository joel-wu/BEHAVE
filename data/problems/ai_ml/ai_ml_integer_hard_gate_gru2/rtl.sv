// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module HardGateGRU2 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [15:0] in_data,
    input logic [95:0] in_weights,
    input logic [95:0] in_recurrent,
    input logic [95:0] in_bias,
    input logic [0:0] in_clear,
    output logic [15:0] out_values
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
function automatic signed [31:0] away(input logic signed [63:0] n, input integer d);
  logic signed [63:0] q;
  begin
    q=n<0 ? -((-n+d/2)/d) : (n+d/2)/d;
    away=q[31:0];
  end
endfunction
logic [15:0] hidden;
logic [15:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : gru
    integer old[0:1]; integer gates[0:3]; integer candidate,updated;
    old[0]=in_clear ? 0:s8(hidden[7:0]); old[1]=in_clear ? 0:s8(hidden[15:8]);
    candidate=0; updated=0;
    for(integer g=0;g<2;g=g+1)
      for(integer i=0;i<2;i=i+1) begin
        value=128*s16(in_bias[16*(2*g+i) +: 16]);
        for(integer j=0;j<2;j=j+1) begin
          value=value+s8(in_weights[8*(4*g+2*i+j) +: 8])*s8(in_data[8*j +: 8]);
          value=value+s8(in_recurrent[8*(4*g+2*i+j) +: 8])*old[j];
        end
        value=away(value,512)+64;
        gates[2*g+i]=value<0 ? 0:value>128 ? 128:value;
      end
    for(integer i=0;i<2;i=i+1) begin
      value=128*s16(in_bias[16*(4+i) +: 16]);
      for(integer j=0;j<2;j=j+1) begin
        value=value+s8(in_weights[8*(8+2*i+j) +: 8])*s8(in_data[8*j +: 8]);
        value=value+s8(in_recurrent[8*(8+2*i+j) +: 8])*away(gates[2+j]*old[j],128);
      end
      candidate=clip8(away(value,128));
      updated=clip8(away((128-gates[i])*candidate+gates[i]*old[i],128));
      result[8*i +: 8]=updated[7:0];
    end
  end
end
assign in_ready = !out_valid || out_ready;
always_ff @(posedge clk) begin
  if (rst) begin
    out_valid <= 0;
    out_values <= 0;
    hidden <= 0;
  end else if (in_ready) begin
    out_valid <= in_valid;
    if (in_valid) out_values <= result;
    if (in_valid) hidden <= result;
  end
end
endmodule
