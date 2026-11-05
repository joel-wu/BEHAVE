
module TopModule (
  input [7:0] value,
  output parity
);

  assign parity = ^value;

endmodule
