// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module GatherNDBatch2 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [511:0] in_data,
    input logic [35:0] in_indices,
    output logic [95:0] out_values
);


logic [95:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer b=0;b<2;b=b+1)
    for(integer q=0;q<3;q=q+1) begin
      integer x,y;
      x=in_indices[6*(3*b+q) +: 3]; y=in_indices[6*(3*b+q)+3 +: 3];
      if(x>=4) x=x-4; if(y>=4) y=y-4;
      for(integer c=0;c<2;c=c+1)
        result[8*(6*b+2*q+c) +: 8]=in_data[8*(((4*b+x)*4+y)*2+c) +: 8];
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
