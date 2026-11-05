
module TopModule (
  input [31:0] value,
  output [31:0] out
);

  assign out = {value[7:0], value[15:8], value[23:16], value[31:24]};

endmodule
