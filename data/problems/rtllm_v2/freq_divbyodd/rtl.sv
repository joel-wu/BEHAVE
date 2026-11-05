module freq_divbyodd #(parameter int NUM_DIV = 5)(
    input logic clock_level, rst_n, output wire clk_div
);
    localparam int W=$clog2(NUM_DIV);
    logic [W-1:0] rising_count=0, falling_count=0;
    logic positive=1, negative=1;
    always_ff @(posedge clock_level or negedge rst_n) begin
        if(!rst_n) begin rising_count<=0; positive<=1; end
        else begin
            positive<=rising_count < NUM_DIV/2;
            rising_count<=rising_count==NUM_DIV-1 ? 0 : rising_count+1'b1;
        end
    end
    always_ff @(negedge clock_level or negedge rst_n) begin
        if(!rst_n) begin falling_count<=0; negative<=1; end
        else begin
            negative<=falling_count < NUM_DIV/2;
            falling_count<=falling_count==NUM_DIV-1 ? 0 : falling_count+1'b1;
        end
    end
    assign clk_div=positive|negative;
endmodule
