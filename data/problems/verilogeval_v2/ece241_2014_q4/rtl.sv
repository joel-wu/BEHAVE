module TopModule (input logic clk, init_reset, x, output logic z);
    logic a, b, c;
    assign z=!(a|b|c);
    always_ff @(posedge clk) begin
        if(init_reset) begin a<=0; b<=0; c<=0; end
        else begin a<=a^x; b<=x&!b; c<=x|!c; end
    end
endmodule
