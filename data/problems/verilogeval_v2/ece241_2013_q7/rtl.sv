module TopModule (input logic clk, init_reset, j, k, output logic Q);
    always_ff @(posedge clk) if(init_reset) Q<=0; else Q<=(j&~Q)|(~k&Q);
endmodule
