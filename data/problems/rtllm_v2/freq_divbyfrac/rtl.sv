module freq_divbyfrac #(parameter int MUL2_DIV_CLK = 7)(
    input logic clock_level, rst_n, output wire clk_div
);
    localparam int W=$clog2(MUL2_DIV_CLK);
    logic [W-1:0] count=0;
    logic positive=0, negative=0;
    always_ff @(posedge clock_level or negedge rst_n) begin
        if(!rst_n) begin count<=0; positive<=0; end
        else begin
            positive<=(count==0 || count==MUL2_DIV_CLK/2+1);
            count<=count==MUL2_DIV_CLK-1 ? 0 : count+1'b1;
        end
    end
    always_ff @(negedge clock_level or negedge rst_n) begin
        if(!rst_n) negative<=0;
        else negative<=(count==1 || count==MUL2_DIV_CLK/2+1);
    end
    assign clk_div=positive|negative;
endmodule
