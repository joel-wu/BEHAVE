
module TopModule (
  input [254:0] value,
  output reg [7:0] out
);

  always_comb  begin
    out = 0;
    for (int i=0;i<255;i++)
      out = out + value[i];
  end

endmodule
