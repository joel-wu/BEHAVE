// SPDX-License-Identifier: Zlib
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module CanonicalHuffman16 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_lengths,
    output logic [128:0] out_values
);


logic [128:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : canonical_codes
    integer counts[0:8]; integer next_codes[0:8];
    integer invalid_v,left,code,length,rev;
    invalid_v=0; left=1; code=0; length=0; rev=0;
    for(integer i=0;i<9;i=i+1) begin counts[i]=0; next_codes[i]=0; end
    for(integer i=0;i<16;i=i+1) begin
      length=in_lengths[4*i +: 4];
      if(length>8) invalid_v=1; else if(length>0) counts[length]=counts[length]+1;
    end
    for(integer i=1;i<=8;i=i+1) begin
      left=2*left-counts[i]; if(left<0) invalid_v=1;
      code=(code+counts[i-1])*2; next_codes[i]=code;
    end
    if(invalid_v) result[128]=1;
    else for(integer i=0;i<16;i=i+1) begin
      length=in_lengths[4*i +: 4];
      if(length>0) begin
        code=next_codes[length]; next_codes[length]=code+1; rev=0;
        for(integer j=0;j<8;j=j+1) if(j<length) rev=(rev << 1) | ((code >> j)&1);
        result[8*i +: 8]=rev[7:0];
      end
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
