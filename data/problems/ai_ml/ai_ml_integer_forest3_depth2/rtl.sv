// SPDX-License-Identifier: BSD-3-Clause
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module Forest3Depth2 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [31:0] in_features,
    input logic [3:0] in_missing,
    input logic [17:0] in_node_features,
    input logic [71:0] in_thresholds,
    input logic [8:0] in_default_left,
    input logic [191:0] in_leaves,
    output logic [63:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction
function automatic signed [31:0] s16(input logic [15:0] x);
  s16 = {{16{x[15]}}, x};
endfunction
function automatic signed [31:0] away_ratio(input logic signed [63:0] n,d);
  logic signed [63:0] a,q;
  begin a=n<0 ? -n:n; q=(2*a+d)/(2*d); away_ratio=n<0 ? -q:q; end
endfunction

logic [63:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : forest
    integer total,node,leaf,site,f,left,branch;
    total=0;
    for(integer tree=0;tree<3;tree=tree+1) begin
      node=0;leaf=0;
      for(integer depth=0;depth<2;depth=depth+1) begin
        site=3*tree+node;f=in_node_features[2*site +: 2];
        left=in_missing[f] ? in_default_left[site]:(s8(in_features[8*f +: 8])<=s8(in_thresholds[8*site +: 8]));
        branch=left ? 0:1;leaf=2*leaf+branch;node=1+branch;
      end
      value=s16(in_leaves[16*(4*tree+leaf) +: 16]);result[16*tree +: 16]=value;total=total+value;
    end
    result[48 +: 16]=away_ratio(total,3);
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
