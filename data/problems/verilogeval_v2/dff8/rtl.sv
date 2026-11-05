module TopModule (input logic clk, init_reset, input logic [7:0] d, output logic [7:0] q);
    always_ff @(posedge clk) if(init_reset) q<=0; else q<=d;
endmodule
