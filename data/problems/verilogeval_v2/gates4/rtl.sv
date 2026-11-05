
module TopModule (
  input [3:0] value,
  output out_and,
  output out_or,
  output out_xor
);

  assign out_and = &value;
  assign out_or = |value;
  assign out_xor = ^value;

endmodule
