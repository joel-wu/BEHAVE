module TopModule (input logic clk, init_reset, L, q_in, r_in, output logic Q);
    always_ff @(posedge clk) if(init_reset) Q<=0; else Q<=L ? r_in : q_in;
endmodule
