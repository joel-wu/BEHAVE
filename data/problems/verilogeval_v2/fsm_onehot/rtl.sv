
module TopModule (
  input value,
  input [9:0] state,
  output [9:0] next_state,
  output out1,
  output out2
);

  assign out1 = state[8] | state[9];
  assign out2 = state[7] | state[9];

  assign next_state[0] = !value && (|state[4:0] | state[7] | state[8] | state[9]);
  assign next_state[1] = value && (state[0] | state[8] | state[9]);
  assign next_state[2] = value && state[1];
  assign next_state[3] = value && state[2];
  assign next_state[4] = value && state[3];
  assign next_state[5] = value && state[4];
  assign next_state[6] = value && state[5];
  assign next_state[7] = value && (state[6] | state[7]);
  assign next_state[8] = !value && state[5];
  assign next_state[9] = !value && state[6];

endmodule
