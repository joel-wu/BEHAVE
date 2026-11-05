// SPDX-License-Identifier: CC0-1.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module SipHash24Short15 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_key,
    input logic [119:0] in_message,
    input logic [3:0] in_length,
    output logic [63:0] out_values
);


function automatic [63:0] rotl64(input logic [63:0] x, input integer n);
  rotl64=(x<<n)|(x>>(64-n));
endfunction

logic [63:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : siphash
    logic [63:0] v0,v1,v2,v3,word;
    v0=64'h736f6d6570736575^in_key[63:0];
    v1=64'h646f72616e646f6d^in_key[127:64];
    v2=64'h6c7967656e657261^in_key[63:0];
    v3=64'h7465646279746573^in_key[127:64]; word=0;
    for(integer phase=0;phase<3;phase=phase+1) if(phase!=0 || in_length>=8) begin
      word=0;
      if(phase==0) word=in_message[63:0];
      else if(phase==1) begin
        word={4'd0,in_length,56'd0};
        for(integer j=0;j<7;j=j+1) if(j<(in_length%8))
          word[8*j +: 8]=in_message >> (8*((in_length/8)*8+j));
      end
      if(phase<2) v3=v3^word; else v2=v2^64'hff;
      for(integer r=0;r<4;r=r+1) if(phase==2 || r<2) begin
    v0=v0+v1; v1=rotl64(v1,13)^v0; v0=rotl64(v0,32);
    v2=v2+v3; v3=rotl64(v3,16)^v2;
    v0=v0+v3; v3=rotl64(v3,21)^v0;
    v2=v2+v1; v1=rotl64(v1,17)^v2; v2=rotl64(v2,32);
      end
      if(phase<2) v0=v0^word;
    end
    result=v0^v1^v2^v3;
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
