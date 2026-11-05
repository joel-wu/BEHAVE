module TopModule (input logic clk, init_reset, load, input logic [9:0] data, output logic tc);
    logic [9:0] count_q;
    assign tc=(count_q==0);
    always_ff @(posedge clk) if(init_reset) count_q<=0; else if(load) count_q<=data;
        else if(count_q!=0) count_q<=count_q-10'd1;
endmodule
