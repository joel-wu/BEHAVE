
module TopModule (
  input value,
  input [1:0] state,
  output reg [1:0] next_state,
  output out
);

  parameter A=0, B=1, C=2, D=3;

  always_comb begin
    case (state)
      A: next_state = value ? B : A;
      B: next_state = value ? B : C;
      C: next_state = value ? D : A;
      D: next_state = value ? B : C;
    endcase
  end

  assign out = (state==D);

endmodule
