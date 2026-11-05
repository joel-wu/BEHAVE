module TopModule (
  input clk,
  input value,
  input reset,
  output out
);
parameter A=0, B=1;
  reg state;
  reg next;

    always_comb begin
    case (state)
      A: next = value ? A : B;
      B: next = value ? B : A;
    endcase
    end

    always @(posedge clk) begin
    if (reset) state <= B;
        else state <= next;
  end

  assign out = (state==B);
endmodule
