// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module BilinearResize3To5 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [71:0] in_data,
    output logic [199:0] out_values
);

logic [199:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer h=0;h<5;h=h+1) begin
    for(integer w=0;w<5;w=w+1) begin
      integer i,j,i1,j1,a,b;
      i=h/2; j=w/2; i1=i==2 ? 2:i+1; j1=j==2 ? 2:j+1; a=h%2; b=w%2;
      value=(2-a)*(2-b)*32'(in_data[8*(3*i+j) +: 8]);
      value=value+(2-a)*b*32'(in_data[8*(3*i+j1) +: 8]);
      value=value+a*(2-b)*32'(in_data[8*(3*i1+j) +: 8]);
      value=value+a*b*32'(in_data[8*(3*i1+j1) +: 8]);
      result[8*(5*h+w) +: 8]=8'((value+2)/4);
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
