module TopModule (input logic clk, init_reset, enable, S, A, B, C, output logic Z);
    logic [7:0] bits_q;
    assign Z=bits_q[{A,B,C}];
    always_ff @(posedge clk) if(init_reset) bits_q<=0; else if(enable) bits_q<={bits_q[6:0],S};
endmodule
