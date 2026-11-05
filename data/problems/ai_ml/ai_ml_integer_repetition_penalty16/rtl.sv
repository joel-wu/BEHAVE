// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module RepetitionPenalty16 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [31:0] in_tokens,
    input logic [3:0] in_length,
    input logic [255:0] in_scores,
    output logic [271:0] out_values
);
function automatic signed [31:0] s16(input logic [15:0] x);
  s16 = {{16{x[15]}}, x};
endfunction
function automatic signed [31:0] even_ratio(input logic signed [63:0] n,d);
  logic signed [63:0] a,q,r;
  begin
    a=n<0 ? -n:n; q=a/d; r=a-q*d;
    if(2*r>d || (2*r==d && q[0])) q=q+1;
    even_ratio=n<0 ? -q:q;
  end
endfunction
function automatic signed [31:0] clip16(input logic signed [63:0] x);
  clip16=x< -32768 ? -32768:x>32767 ? 32767:x;
endfunction

logic [271:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : repetition
    integer seen;
    seen=0;for(integer j=0;j<8;j=j+1) if(j<in_length) seen=seen|(1<<in_tokens[4*j +: 4]);
    result[256 +: 16]=seen;
    for(integer v=0;v<16;v=v+1) begin
      value=s16(in_scores[16*v +: 16]);
      if(seen&(1<<v)) begin
        value=value<0 ? even_ratio(3*value,2):even_ratio(2*value,3);value=clip16(value);
      end
      result[16*v +: 16]=value;
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
