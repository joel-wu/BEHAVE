// SPDX-License-Identifier: BSD-3-Clause
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module WeightedEmbeddingBags2 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_table,
    input logic [23:0] in_indices,
    input logic [63:0] in_weights,
    input logic [5:0] in_lengths,
    output logic [255:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction

logic [255:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : embedding_bags
    integer length,slot,row_index;
    for(integer b=0;b<2;b=b+1) begin
      length=in_lengths[3*b +: 3];if(length>4) length=4;
      for(integer c=0;c<4;c=c+1) begin
        value=0;
        for(integer j=0;j<4;j=j+1) if(j<length) begin
          slot=4*b+j;row_index=in_indices[3*slot +: 3];
          value=value+s8(in_weights[8*slot +: 8])*s8(in_table[8*(4*row_index+c) +: 8]);
        end
        result[32*(4*b+c) +: 32]=value;
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
