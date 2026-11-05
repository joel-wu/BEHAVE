module TopModule (input logic clk, init_reset, d, output logic q);
    always_ff @(posedge clk) if(init_reset) q<=0; else q<=d;
endmodule
