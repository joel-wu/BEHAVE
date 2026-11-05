module TopModule (input logic clk, init_reset, load, input logic [1:0] ena, input logic [99:0] data, output logic [99:0] q);
    always_ff @(posedge clk) if(init_reset) q<=0; else if(load) q<=data; else if(ena==1) q<={q[0],q[99:1]}; else if(ena==2) q<={q[98:0],q[99]};
endmodule
