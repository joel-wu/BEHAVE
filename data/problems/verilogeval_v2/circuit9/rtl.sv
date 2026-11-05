module TopModule (input logic clk, init_reset, a, output logic [2:0] q);
    always_ff @(posedge clk) if(init_reset) q<=0; else if(a) q<=3'd4; else if(q==6) q<=0; else q<=q+3'd1;
endmodule
