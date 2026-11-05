// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module L1NormalizeQ14 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_data,
    output logic [127:0] out_values
);
function automatic signed [31:0] s16(input logic [15:0] x);
  s16 = {{16{x[15]}}, x};
endfunction
logic [127:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : normalize
    integer denominator;
    denominator=0;
    for(integer i=0;i<8;i=i+1) begin
      value=s16(in_data[16*i +: 16]);
      denominator=denominator+(value<0 ? -value : value);
    end
    if(denominator!=0) begin
      for(integer i=0;i<8;i=i+1) begin
        value=s16(in_data[16*i +: 16]);
        sample=value<0 ? -value : value;
        sample=(32768*sample+denominator)/(2*denominator);
        result[16*i +: 16]=16'(value<0 ? -sample : sample);
      end
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
