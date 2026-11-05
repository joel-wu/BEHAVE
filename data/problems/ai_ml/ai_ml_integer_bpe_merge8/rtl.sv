// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module BPEMerge8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [31:0] in_tokens,
    input logic [3:0] in_length,
    input logic [95:0] in_rules,
    input logic [7:0] in_rule_mask,
    output logic [38:0] out_values
);


logic [38:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : bpe
    integer tokens[0:7];integer length,merges,best_rule,best_pos,replacement,rule;
    length=in_length>8 ? 8:in_length;merges=0;
    for(integer i=0;i<8;i=i+1) tokens[i]=in_tokens[4*i +: 4];
    for(integer iteration=0;iteration<7;iteration=iteration+1) begin
      best_rule=8;best_pos=0;replacement=0;
      for(integer r=0;r<8;r=r+1) if(in_rule_mask[r]) begin
        rule=in_rules[12*r +: 12];
        for(integer j=0;j<7;j=j+1) if(j+1<length && tokens[j]==(rule&15) && tokens[j+1]==((rule>>4)&15) && r<best_rule) begin
          best_rule=r;best_pos=j;replacement=(rule>>8)&15;
        end
      end
      if(best_rule<8) begin
        tokens[best_pos]=replacement;
        for(integer j=0;j<7;j=j+1) if(j>best_pos) tokens[j]=tokens[j+1];
        length=length-1;merges=merges+1;
      end
    end
    result[32 +: 4]=length;result[36 +: 3]=merges;
    for(integer i=0;i<8;i=i+1) if(i<length) result[4*i +: 4]=tokens[i];
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
