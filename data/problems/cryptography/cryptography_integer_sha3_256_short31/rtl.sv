// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module SHA3_256Short31 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [247:0] in_message,
    input logic [4:0] in_length,
    output logic [255:0] out_values
);


function automatic [63:0] rol64(input logic [63:0] x,input integer n);
  rol64=(x<<n)|(x>>(64-n));
endfunction

logic [255:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : sha3
    logic [63:0] state[0:24];logic [63:0] columns[0:4];logic [63:0] mixed[0:24];logic [63:0] d;
    logic [149:0] rotations;logic [1535:0] constants;
    rotations=150'd332055894658593304689340699888832964872888384;constants=1536'h8000000080008008000000008000000180000000000080808000000080008081800000008000000a000000000000800a8000000000000080800000000000800280000000000080038000000000008089800000000000008b000000008000808b000000008000000a00000000800080090000000000000088000000000000008a800000000000800980000000800080810000000080000001000000000000808b8000000080008000800000000000808a00000000000080820000000000000001;
    for(integer i=0;i<25;i=i+1) state[i]=0;
    for(integer i=0;i<31;i=i+1) if(i<in_length) state[i/8][8*(i%8) +: 8]=in_message[8*i +: 8];
    state[in_length/8]=state[in_length/8]^(64'd6<<(8*(in_length%8)));state[16]=state[16]^64'h8000000000000000;
    for(integer r=0;r<24;r=r+1) begin
      for(integer x=0;x<5;x=x+1) columns[x]=state[x]^state[x+5]^state[x+10]^state[x+15]^state[x+20];
      for(integer x=0;x<5;x=x+1) begin
        d=columns[(x+4)%5]^rol64(columns[(x+1)%5],1);
        for(integer y=0;y<5;y=y+1) state[x+5*y]=state[x+5*y]^d;
      end
      for(integer x=0;x<5;x=x+1) for(integer y=0;y<5;y=y+1)
        mixed[y+5*((2*x+3*y)%5)]=rol64(state[x+5*y],rotations[6*(x+5*y) +: 6]);
      for(integer x=0;x<5;x=x+1) for(integer y=0;y<5;y=y+1)
        state[x+5*y]=mixed[x+5*y]^((~mixed[(x+1)%5+5*y])&mixed[(x+2)%5+5*y]);
      state[0]=state[0]^constants[64*r +: 64];
    end
    for(integer i=0;i<4;i=i+1) result[64*i +: 64]=state[i];
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
