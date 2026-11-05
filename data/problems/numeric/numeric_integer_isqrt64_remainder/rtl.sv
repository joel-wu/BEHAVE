// SPDX-License-Identifier: Unlicense
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module ISqrt64Remainder (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_n,
    output logic [64:0] out_values
);


logic [64:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : sqrt_bits
    logic [65:0] rest,trial;
    logic [32:0] root_v;
    rest=0; trial=0; root_v=0;
    for(integer i=0;i<32;i=i+1) begin
      rest=(rest << 2) | {64'd0,in_n[62-2*i +: 2]};
      trial=({33'd0,root_v} << 2) | 66'd1;
      root_v=root_v << 1;
      if(rest>=trial) begin rest=rest-trial; root_v=root_v | 33'd1; end
    end
    result[31:0]=root_v[31:0]; result[64:32]=rest[32:0];
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
