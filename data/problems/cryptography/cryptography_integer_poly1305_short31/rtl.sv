// SPDX-License-Identifier: BSD-2-Clause
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module Poly1305Short31 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_key,
    input logic [247:0] in_message,
    input logic [4:0] in_length,
    output logic [127:0] out_values
);



logic [127:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : authenticate
    logic [127:0] r,s;
    logic [129:0] h;
    logic [128:0] chunk;
    logic [257:0] product;
    integer count;
    r=in_key[127:0]&128'h0ffffffc0ffffffc0ffffffc0fffffff;
    s=in_key[255:128]; h=0; chunk=0; product=0; count=0;
    for(integer block=0;block<2;block=block+1) if(in_length>16*block) begin
      count=in_length-16*block;
      if(count>16) count=16;
      chunk=0;
      for(integer j=0;j<16;j=j+1) if(j<count)
        chunk[8*j +: 8]=in_message >> (8*(16*block+j));
      chunk[8*count]=1'b1;
      product=({128'd0,h}+{129'd0,chunk})*{130'd0,r};
      h=product%258'd1361129467683753853853498429727072845819;
    end
    result=h[127:0]+s;
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
