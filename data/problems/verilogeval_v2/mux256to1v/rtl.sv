
module TopModule (
  input [1023:0] value,
  input [7:0] sel,
  output [3:0] out
);

  assign out = {value[sel*4+3], value[sel*4+2], value[sel*4+1], value[sel*4+0]};

endmodule
