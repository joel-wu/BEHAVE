// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module NonZeroCoordinates3x4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [95:0] in_data,
    output logic [51:0] out_values
);


logic [51:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : compact
    integer count;
    count=0;
    for(integer r=0;r<3;r=r+1)
      for(integer c=0;c<4;c=c+1)
        if(in_data[8*(4*r+c) +: 8]!=0) begin
          result[2*count +: 2]=r; result[24+2*count +: 2]=c; count=count+1;
        end
    result[48 +: 4]=count;
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
