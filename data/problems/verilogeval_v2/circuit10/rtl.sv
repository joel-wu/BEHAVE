module TopModule (input logic clk, init_reset, a, b, output logic q, state);
    logic carry_q;
    assign q=a^b^carry_q;
    assign state=carry_q;
    always_ff @(posedge clk) if(init_reset) carry_q<=0;
        else carry_q<=(a&b)|(a&carry_q)|(b&carry_q);
endmodule
