
module TopModule (
  input  [99:0] value,
  output [99:0] out_both,
  output [99:0] out_any,
  output [99:0] out_different
);

  assign out_both = { 1'b0, (value[98:0] & value[99:1]) };

  assign out_any = { (value[98:0] | value[99:1]), 1'b0 };

  assign out_different = value ^ { value[0], value[99:1] };

endmodule
