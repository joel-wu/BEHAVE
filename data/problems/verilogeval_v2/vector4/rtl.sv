
module TopModule (
  input [7:0] value,
  output [31:0] out
);

  assign out = { {24{value[7]}}, value };

endmodule
