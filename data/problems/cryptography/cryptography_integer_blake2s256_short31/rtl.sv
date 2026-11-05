// SPDX-License-Identifier: CC0-1.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module Blake2s256Short31 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [247:0] in_message,
    input logic [4:0] in_length,
    output logic [255:0] out_values
);


function automatic [31:0] rotr32(input logic [31:0] x, input integer n);
  rotr32=(x>>n)|(x<<(32-n));
endfunction

logic [255:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : digest
    localparam logic [639:0] SIGMA=640'h0dc3e9bf5167482a5a417d2c803b9ef6a2684f05931ce7bdb8293670a4def15c91ef57d438b0a6c2d386cb1efa4275098f04a562ebcd1397491763eadf250c8b357b20c16df984aefedcba9876543210;
    localparam logic [255:0] IV=256'h5be0cd191f83d9ab9b05688c510e527fa54ff53a3c6ef372bb67ae856a09e667;
    logic [31:0] h[0:7],v[0:15],message[0:15],x,y;
    integer a,b,c,d;
    a=0; b=0; c=0; d=0; x=0; y=0;
    for(integer i=0;i<16;i=i+1) begin v[i]=0; message[i]=0; end
    for(integer i=0;i<31;i=i+1) if(i<in_length)
      message[i/4][8*(i%4) +: 8]=in_message[8*i +: 8];
    for(integer i=0;i<8;i=i+1) h[i]=IV[32*i +: 32];
    h[0]=h[0]^32'h01010020;
    for(integer i=0;i<8;i=i+1) begin v[i]=h[i]; v[8+i]=IV[32*i +: 32]; end
    v[12]=v[12]^{27'd0,in_length}; v[14]=v[14]^32'hffffffff;
    for(integer r=0;r<10;r=r+1) for(integer j=0;j<8;j=j+1) begin
      a=j%4; b=4+((j<4)?a:(a+1)%4); c=8+((j<4)?a:(a+2)%4); d=12+((j<4)?a:(a+3)%4);
      x=message[SIGMA[4*(16*r+2*j) +: 4]]; y=message[SIGMA[4*(16*r+2*j+1) +: 4]];
      v[a]=v[a]+v[b]+x; v[d]=rotr32(v[d]^v[a],16);
      v[c]=v[c]+v[d]; v[b]=rotr32(v[b]^v[c],12);
      v[a]=v[a]+v[b]+y; v[d]=rotr32(v[d]^v[a],8);
      v[c]=v[c]+v[d]; v[b]=rotr32(v[b]^v[c],7);
    end
    for(integer i=0;i<8;i=i+1) result[32*i +: 32]=h[i]^v[i]^v[8+i];
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
