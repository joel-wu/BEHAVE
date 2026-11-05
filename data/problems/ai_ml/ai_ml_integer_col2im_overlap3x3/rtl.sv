// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module Col2ImOverlap3x3 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_columns,
    output logic [143:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction
logic [143:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer y=0;y<3;y=y+1) begin
    for(integer x=0;x<3;x=x+1) begin
      value=0;
      for(integer h=0;h<2;h=h+1) begin
        for(integer w=0;w<2;w=w+1) begin
          integer i,j;
          i=y-h; j=x-w;
          if(i>=0 && i<2 && j>=0 && j<2)
            value=value+s8(in_columns[8*(4*(2*i+j)+2*h+w) +: 8]);
        end
      end
      result[16*(3*y+x) +: 16]=16'(value);
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
