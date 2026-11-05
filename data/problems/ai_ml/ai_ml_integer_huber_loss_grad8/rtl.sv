// SPDX-License-Identifier: BSD-3-Clause
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module HuberLossGrad8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_prediction,
    input logic [127:0] in_target,
    input logic [7:0] in_delta_minus1,
    output logic [159:0] out_values
);
function automatic signed [31:0] s16(input logic [15:0] x);
  s16 = {{16{x[15]}}, x};
endfunction

logic [159:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : huber
    integer total,d,e,a,gradient;
    total=0;d=in_delta_minus1+1;
    for(integer i=0;i<8;i=i+1) begin
      e=s16(in_prediction[16*i +: 16])-s16(in_target[16*i +: 16]);a=e<0 ? -e:e;
      total=total+(a<=d ? e*e:2*d*a-d*d);gradient=e< -d ? -d:e>d ? d:e;
      result[32+16*i +: 16]=gradient;
    end
    result[31:0]=total;
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
