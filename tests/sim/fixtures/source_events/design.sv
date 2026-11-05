module M(input clk, rst, input [1:0] sel,
         output reg [7:0] q, output reg [7:0] seen);
    initial begin q = 7; seen = 0; end
    wire take = clk && q == 0;
    always @(posedge clk or posedge rst) begin
        if (rst) q <= 0;
        else if (take) q <= 5;
        else q <= q + 2;
    end
    always @(posedge clk) begin
        if (q == 0) seen <= 9;
        else case (sel)
            0: seen <= q;
            1: seen <= q + 1;
            2: seen <= q + 2;
        endcase
    end
endmodule
