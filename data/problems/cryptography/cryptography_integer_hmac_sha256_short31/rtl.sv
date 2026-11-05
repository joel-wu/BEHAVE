// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module HMACSHA256Short31 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_key,
    input logic [247:0] in_message,
    input logic [4:0] in_length,
    output logic [255:0] out_values
);


function automatic [31:0] ror32(input logic [31:0] x,input integer n);
  ror32=(x>>n)|(x<<(32-n));
endfunction
function automatic [255:0] sha256_two_blocks(input logic [1023:0] data);
  logic [31:0] states[0:7];logic [31:0] words[0:63];logic [31:0] a,b,c,d,e,f,g,h,s0,s1,t1,t2;
  logic [2047:0] constants;logic [255:0] initial_words;
  begin
    constants=2048'hc67178f2bef9a3f7a4506ceb90befffa8cc7020884c8781478a5636f748f82ee682e6ff35b9cca4f4ed8aa4a391c0cb334b0bcb52748774c1e376c0819a4c116106aa070f40e3585d6990624d192e819c76c51a3c24b8b70a81a664ba2bfe8a192722c8581c2c92e766a0abb650a735453380d134d2c6dfc2e1b213827b70a851429296706ca6351d5a79147c6e00bf3bf597fc7b00327c8a831c66d983e515276f988da5cb0a9dc4a7484aa2de92c6f240ca1cc0fc19dc6efbe4786e49b69c1c19bf1749bdc06a780deb1fe72be5d74550c7dc3243185be12835b01d807aa98ab1c5ed5923f82a459f111f13956c25be9b5dba5b5c0fbcf71374491428a2f98;initial_words=256'h5be0cd191f83d9ab9b05688c510e527fa54ff53a3c6ef372bb67ae856a09e667;
    for(integer i=0;i<8;i=i+1) states[i]=initial_words[32*i +: 32];
    for(integer block=0;block<2;block=block+1) begin
      for(integer i=0;i<16;i=i+1) begin
        words[i]=0;for(integer j=0;j<4;j=j+1) words[i][8*(3-j) +: 8]=data[8*(64*block+4*i+j) +: 8];
      end
      for(integer i=16;i<64;i=i+1) begin
        s0=ror32(words[i-15],7)^ror32(words[i-15],18)^(words[i-15]>>3);
        s1=ror32(words[i-2],17)^ror32(words[i-2],19)^(words[i-2]>>10);
        words[i]=words[i-16]+s0+words[i-7]+s1;
      end
      a=states[0];b=states[1];c=states[2];d=states[3];e=states[4];f=states[5];g=states[6];h=states[7];
      for(integer i=0;i<64;i=i+1) begin
        t1=h+(ror32(e,6)^ror32(e,11)^ror32(e,25))+((e&f)^((~e)&g))+constants[32*i +: 32]+words[i];
        t2=(ror32(a,2)^ror32(a,13)^ror32(a,22))+((a&b)^(a&c)^(b&c));
        h=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;
      end
      states[0]=states[0]+a;states[1]=states[1]+b;states[2]=states[2]+c;states[3]=states[3]+d;
      states[4]=states[4]+e;states[5]=states[5]+f;states[6]=states[6]+g;states[7]=states[7]+h;
    end
    sha256_two_blocks=0;
    for(integer i=0;i<8;i=i+1) for(integer j=0;j<4;j=j+1) sha256_two_blocks[8*(4*i+j) +: 8]=states[i][8*(3-j) +: 8];
  end
endfunction

logic [255:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : hmac
    logic [1023:0] inner,outer;logic [255:0] digest;integer key_byte,bit_length;
    inner=0;outer=0;
    for(integer i=0;i<64;i=i+1) begin
      key_byte=0;if(i<32) key_byte=in_key[8*i +: 8];
      inner[8*i +: 8]=key_byte^54;outer[8*i +: 8]=key_byte^92;
    end
    for(integer i=0;i<31;i=i+1) if(i<in_length) inner[512+8*i +: 8]=in_message[8*i +: 8];
    inner[8*(64+in_length) +: 8]=8'h80;bit_length=(64+in_length)*8;
    for(integer i=0;i<8;i=i+1) inner[8*(120+i) +: 8]=bit_length>>(8*(7-i));
    digest=sha256_two_blocks(inner);outer[512 +: 256]=digest;outer[768 +: 8]=8'h80;outer[1008 +: 8]=8'h03;
    result=sha256_two_blocks(outer);
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
