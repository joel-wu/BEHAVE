module TopModule (input logic clk, init_reset, a, b, output logic out_assign, out_always_comb, out_always_ff);
    assign out_assign=a^b;
    always_comb out_always_comb=a^b;
    always_ff @(posedge clk) if(init_reset) out_always_ff<=0; else out_always_ff<=a^b;
endmodule
