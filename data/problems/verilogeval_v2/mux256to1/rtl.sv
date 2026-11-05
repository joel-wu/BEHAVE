
module TopModule (
  input [255:0] value,
  input [7:0] sel,
  output  out
);

  assign out = value[sel];

endmodule
