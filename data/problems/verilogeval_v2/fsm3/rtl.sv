module TopModule (
  input clk,
  input value,
  input areset,
  output out
);
parameter A=0, B=1, C=2, D=3;
  reg [1:0] state;
  reg [1:0] next;

  always_comb begin
  case (state)
    A: next = value ? B : A;
    B: next = value ? B : C;
    C: next = value ? D : A;
    D: next = value ? B : C;
  endcase
  end

  always @(posedge clk, posedge areset) begin
    if (areset) state <= A;
      else state <= next;
  end

  assign out = (state==D);
endmodule
