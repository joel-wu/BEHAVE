
module TopModule (
  input [2:0] value,
  output [1:0] out
);

  assign out = value[0]+value[1]+value[2];

endmodule
