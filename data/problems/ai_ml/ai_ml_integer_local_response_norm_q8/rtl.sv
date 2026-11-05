// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module LocalResponseNormQ8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [159:0] in_data,
    output logic [319:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction
function automatic signed [31:0] away(input logic signed [63:0] n, input integer d);
  logic signed [63:0] q;
  begin
    q=n<0 ? -((-n+d/2)/d) : (n+d/2)/d;
    away=q[31:0];
  end
endfunction

logic [319:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer c=0;c<5;c=c+1)
    for(integer p=0;p<4;p=p+1) begin
      integer denom;
      denom=16384;
      for(integer j=0;j<5;j=j+1)
        if(j>=c-1 && j<=c+1) begin
          sample=s8(in_data[8*(4*j+p) +: 8]); denom=denom+sample*sample;
        end
      value=away(64'sd4194304*s8(in_data[8*(4*c+p) +: 8]),denom);
      result[16*(4*c+p) +: 16]=value[15:0];
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
