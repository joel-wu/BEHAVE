
module TopModule (
  input [99:0] value,
  output reg [99:0] out
);

  always_comb
    for (int i=0;i<$bits(out);i++)
      out[i] = value[$bits(out)-i-1];

endmodule
