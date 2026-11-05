module TopModule (input logic clk, init_reset, load, input logic [511:0] data, output logic [511:0] q);
    wire [511:0] left_bits={1'b0,q[511:1]};
    wire [511:0] right_bits={q[510:0],1'b0};
    always_ff @(posedge clk) if(init_reset) q<=0; else if(load) q<=data;
        else q<=left_bits^right_bits;
endmodule
