
module TopModule (
  input  [3:0] value,
  output [3:0] out_both,
  output [3:0] out_any,
  output [3:0] out_different
);

  assign out_both[2:0] = value[2:0] & value[3:1];
  assign out_any[3:1]  = value[2:0] | value[3:1];
  assign out_different = value^{value[0], value[3:1]};

  // we don't care about out_both[3] or out_any[0]
  assign out_both[3] = 1'b0;
  assign out_any[0]  = 1'b0;

endmodule
