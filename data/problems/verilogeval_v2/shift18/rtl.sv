module TopModule (input logic clk, init_reset, load, input logic ena, input logic [1:0] amount, input logic [63:0] data, output logic [63:0] q);
    always_ff @(posedge clk) begin
        if(init_reset) q<=0;
        else if(load) q<=data;
        else if(ena) case(amount)
            0: q<={q[62:0],1'b0};
            1: q<={q[55:0],8'b0};
            2: q<={q[63],q[63:1]};
            3: q<={{8{q[63]}},q[63:8]};
        endcase
    end
endmodule
