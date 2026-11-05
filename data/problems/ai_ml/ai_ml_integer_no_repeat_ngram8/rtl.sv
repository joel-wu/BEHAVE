// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module NoRepeatNGram8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [31:0] in_tokens,
    input logic [3:0] in_length,
    input logic [1:0] in_ngram_minus1,
    input logic [255:0] in_scores,
    output logic [271:0] out_values
);


logic [271:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : ngram_constraint
    integer length,n,banned,same_prefix,a,b;
    length=in_length>8 ? 8:in_length;n=in_ngram_minus1+1;banned=0;
    for(integer start=0;start<8;start=start+1) if(start+n<=length) begin
      same_prefix=1;
      for(integer j=0;j<3;j=j+1) if(j<n-1) begin
        a=in_tokens[4*(start+j) +: 4];b=in_tokens[4*(length-n+1+j) +: 4];if(a!=b) same_prefix=0;
      end
      if(same_prefix) banned=banned|(1<<in_tokens[4*(start+n-1) +: 4]);
    end
    result[256 +: 16]=banned;
    for(integer v=0;v<16;v=v+1) if(!(banned&(1<<v))) result[16*v +: 16]=in_scores[16*v +: 16];
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
