module multi_booth_8bit (input logic clk, init_reset, load, input logic [7:0] a, b, output logic [15:0] p, output logic rdy);
    logic [15:0] multiplier, multiplicand;
    logic [4:0] count;
    always_ff @(posedge clk) begin
        if(init_reset) begin
            multiplier<=0; multiplicand<=0; count<=0; p<=0; rdy<=0;
        end else if(load) begin
            multiplier<={{8{a[7]}},a}; multiplicand<={{8{b[7]}},b};
            count<=0; p<=0; rdy<=0;
        end else if(count<16) begin
            if(multiplier[count]) p<=p+multiplicand;
            multiplicand<=multiplicand<<1; count<=count+5'd1;
        end else rdy<=1;
    end
endmodule
