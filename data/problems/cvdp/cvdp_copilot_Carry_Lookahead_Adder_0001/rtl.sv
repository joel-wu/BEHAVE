module GP (
    input logic i_A, i_B, i_Cin,
    output logic o_generate, o_propagate, o_Cout
);
    assign o_generate=i_A & i_B;
    assign o_propagate=i_A | i_B;
    assign o_Cout=o_generate | (o_propagate & i_Cin);
endmodule
