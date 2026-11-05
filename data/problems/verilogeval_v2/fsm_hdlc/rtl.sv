module TopModule (
  input clk,
  input reset,
  input value,
  output disc,
  output flag,
  output err
);
parameter [3:0] S0=0, S1=1, S2=2, S3=3, S4=4, S5=5, S6=6, SERR=7, SDISC=8, SFLAG=9;
  reg [3:0] state, next;

  assign disc = state == SDISC;
  assign flag = state == SFLAG;
  assign err = state == SERR;

  always @(posedge clk) begin
    case (state)
      S0: state <= value ? S1 : S0;
      S1: state <= value ? S2 : S0;
      S2: state <= value ? S3 : S0;
      S3: state <= value ? S4 : S0;
      S4: state <= value ? S5 : S0;
      S5: state <= value ? S6 : SDISC;
      S6: state <= value ? SERR : SFLAG;
      SERR: state <= value ? SERR : S0;
      SFLAG: state <= value ? S1 : S0;
      SDISC: state <= value ? S1 : S0;
      default: state <= 'x;
    endcase

    if (reset) state <= S0;
  end
endmodule
