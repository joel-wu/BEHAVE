// SPDX-License-Identifier: BSD-3-Clause
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module CausalDilatedConvBlock4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_history,
    input logic [63:0] in_x,
    input logic [95:0] in_weights,
    output logic [319:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction

logic [319:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : causal_block
    logic [127:0] sequence_values;
    sequence_values={in_x,in_history};result[256 +: 64]=in_x;
    for(integer t=0;t<4;t=t+1) for(integer o=0;o<2;o=o+1) begin
      value=0;
      for(integer c=0;c<2;c=c+1) for(integer k=0;k<3;k=k+1)
        value=value+s8(in_weights[8*(6*o+3*c+k) +: 8])*s8(sequence_values[8*(2*(t+2*k)+c) +: 8]);
      result[32*(2*t+o) +: 32]=value;
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
