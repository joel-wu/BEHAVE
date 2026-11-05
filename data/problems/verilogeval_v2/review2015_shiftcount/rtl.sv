module TopModule (input logic clk, init_reset, shift_ena, count_ena, data, output logic [3:0] q);
    always_ff @(posedge clk) if(init_reset) q<=0; else if(shift_ena) q<={q[2:0],data}; else if(count_ena) q<=q-4'd1;
endmodule
