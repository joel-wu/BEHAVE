// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module BeamExpand4x8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_beam_scores,
    input logic [511:0] in_token_scores,
    input logic [3:0] in_beam_mask,
    input logic [2:0] in_eos,
    output logic [110:0] out_values
);
function automatic signed [31:0] s16(input logic [15:0] x);
  s16 = {{16{x[15]}}, x};
endfunction

logic [110:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : beam_expansion
    logic [31:0] used;integer found,best,index,parent,token,score,count;logic [21:0] slot;
    used=0;count=0;
    for(integer rank=0;rank<4;rank=rank+1) begin
      found=0;best=-65537;index=0;
      for(integer i=0;i<32;i=i+1) begin
        parent=i/8;token=i%8;
        if(in_beam_mask[parent] && token!=in_eos && !used[i]) begin
          score=s16(in_beam_scores[16*parent +: 16])+s16(in_token_scores[16*i +: 16]);
          if(score>best) begin best=score;index=i;found=1;end
        end
      end
      if(found) begin used[index]=1;slot=(index/8)|((index%8)<<2)|((best&131071)<<5);result[22*rank +: 22]=slot;count=count+1;end
    end
    result[88 +: 3]=count;found=0;best=-65537;index=0;
    for(integer p=0;p<4;p=p+1) if(in_beam_mask[p]) begin
      score=s16(in_beam_scores[16*p +: 16])+s16(in_token_scores[16*(8*p+in_eos) +: 16]);
      if(score>best) begin best=score;index=p;found=1;end
    end
    if(found) begin result[91 +: 2]=index;result[93 +: 17]=best;result[110]=1;end
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
