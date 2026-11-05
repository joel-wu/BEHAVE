module right_shifter(input logic clk, init_reset, d, output logic [7:0] q);
    always_ff @(posedge clk) if(init_reset) q<=0; else q<={d,q[7:1]};
endmodule
