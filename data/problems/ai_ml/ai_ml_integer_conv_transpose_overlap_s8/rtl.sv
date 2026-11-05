// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module ConvTransposeOverlapS8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_data,
    input logic [287:0] in_weights,
    input logic [31:0] in_bias,
    output logic [1599:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction
function automatic signed [31:0] s16(input logic [15:0] x);
  s16 = {{16{x[15]}}, x};
endfunction

logic [1599:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer c=0;c<2;c=c+1)
    for(integer y=0;y<5;y=y+1)
      for(integer x=0;x<5;x=x+1) begin
        value=s16(in_bias[16*c +: 16]);
        for(integer k=0;k<2;k=k+1)
          for(integer h=0;h<2;h=h+1)
            for(integer w=0;w<2;w=w+1)
              if(y-2*h>=0 && y-2*h<3 && x-2*w>=0 && x-2*w<3)
                value=value+s8(in_data[8*(4*k+2*h+w) +: 8])*
                  s8(in_weights[8*(18*k+9*c+3*(y-2*h)+x-2*w) +: 8]);
        result[32*(25*c+5*y+x) +: 32]=value;
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
