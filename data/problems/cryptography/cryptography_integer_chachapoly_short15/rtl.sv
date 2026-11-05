// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module ChaChaPolyShort15 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_key,
    input logic [95:0] in_nonce,
    input logic [119:0] in_aad,
    input logic [3:0] in_aad_length,
    input logic [119:0] in_message,
    input logic [3:0] in_length,
    output logic [247:0] out_values
);


function automatic [31:0] rol32(input logic [31:0] x,input integer n);
  rol32=(x<<n)|(x>>(32-n));
endfunction
function automatic [127:0] chacha_quarter(input logic [31:0] x0,x1,x2,x3);
  logic [31:0] a,b,c,d;
  begin
    a=x0;b=x1;c=x2;d=x3;
    a=a+b;d=rol32(d^a,16);c=c+d;b=rol32(b^c,12);
    a=a+b;d=rol32(d^a,8);c=c+d;b=rol32(b^c,7);
    chacha_quarter={d,c,b,a};
  end
endfunction
function automatic [511:0] chacha_block(input logic [255:0] key,input logic [95:0] nonce,input logic [31:0] counter);
  logic [31:0] initial_words[0:15];logic [31:0] words[0:15];
  begin
    initial_words[0]=32'h61707865;initial_words[1]=32'h3320646e;initial_words[2]=32'h79622d32;initial_words[3]=32'h6b206574;
    for(integer i=0;i<8;i=i+1) initial_words[4+i]=key[32*i +: 32];initial_words[12]=counter;
    for(integer i=0;i<3;i=i+1) initial_words[13+i]=nonce[32*i +: 32];
    for(integer i=0;i<16;i=i+1) words[i]=initial_words[i];
    for(integer r=0;r<10;r=r+1) begin
      {words[12],words[8],words[4],words[0]}=chacha_quarter(words[0],words[4],words[8],words[12]);
      {words[13],words[9],words[5],words[1]}=chacha_quarter(words[1],words[5],words[9],words[13]);
      {words[14],words[10],words[6],words[2]}=chacha_quarter(words[2],words[6],words[10],words[14]);
      {words[15],words[11],words[7],words[3]}=chacha_quarter(words[3],words[7],words[11],words[15]);
      {words[15],words[10],words[5],words[0]}=chacha_quarter(words[0],words[5],words[10],words[15]);
      {words[12],words[11],words[6],words[1]}=chacha_quarter(words[1],words[6],words[11],words[12]);
      {words[13],words[8],words[7],words[2]}=chacha_quarter(words[2],words[7],words[8],words[13]);
      {words[14],words[9],words[4],words[3]}=chacha_quarter(words[3],words[4],words[9],words[14]);
    end
    chacha_block=0;for(integer i=0;i<16;i=i+1) chacha_block[32*i +: 32]=words[i]+initial_words[i];
  end
endfunction
function automatic [129:0] poly_reduce(input logic [257:0] product);
  logic [131:0] folded;
  begin
    // 2^130 == 5 modulo p. For a 258-bit product the first fold is
    // < 2^132, the second <= 2^130+9, so one subtraction suffices.
    folded={2'd0,product[129:0]}+132'd5*{4'd0,product[257:130]};
    folded={2'd0,folded[129:0]}+132'd5*(folded>>130);
    if(folded>=132'd1361129467683753853853498429727072845819)
      folded=folded-132'd1361129467683753853853498429727072845819;
    poly_reduce=folded[129:0];
  end
endfunction

logic [247:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : aead
    logic [511:0] key_stream,stream;logic [119:0] ciphertext;
    logic [383:0] mac;logic [127:0] r,s,tag;logic [129:0] h;logic [257:0] product;logic [128:0] block_value;integer count;
    key_stream=chacha_block(in_key,in_nonce,0);stream=chacha_block(in_key,in_nonce,1);ciphertext=0;
    for(integer i=0;i<15;i=i+1) if(i<in_length) ciphertext[8*i +: 8]=in_message[8*i +: 8]^stream[8*i +: 8];
    mac=0;count=0;
    if(in_aad_length>0) begin
      for(integer i=0;i<15;i=i+1) if(i<in_aad_length) mac[8*i +: 8]=in_aad[8*i +: 8];count=count+1;
    end
    if(in_length>0) begin mac[128*count +: 128]={8'd0,ciphertext};count=count+1;end
    mac[128*count +: 64]={60'd0,in_aad_length};mac[128*count+64 +: 64]={60'd0,in_length};count=count+1;
    r=key_stream[127:0]&128'h0ffffffc0ffffffc0ffffffc0fffffff;s=key_stream[255:128];h=0;
    for(integer block=0;block<3;block=block+1) if(block<count) begin
      block_value={1'b1,mac[128*block +: 128]};product=({128'd0,h}+{129'd0,block_value})*{130'd0,r};
      h=poly_reduce(product);
    end
    tag=h[127:0]+s;result={tag,ciphertext};
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
