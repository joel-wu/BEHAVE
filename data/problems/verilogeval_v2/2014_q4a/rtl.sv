module TopModule (input logic clk, init_reset, w, R, E, L, output logic Q);
    always_ff @(posedge clk) if(init_reset) Q<=0; else if(L) Q<=R; else if(E) Q<=w;
endmodule
