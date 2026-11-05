module TopModule (
  input clk,
  input areset,
  input x,
  output z
);
  localparam [1:0] A=2'b01, B=2'b10;
  reg [1:0] state;
  always @(posedge clk or posedge areset)
    if (areset) state <= A;
    else if (state==A && x) state <= B;
  assign z = (state==A) ? x : ~x;
endmodule
