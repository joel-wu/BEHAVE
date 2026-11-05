module perfect_squares_generator (
    input logic clk, reset,
    output logic [31:0] sqr_o
);
    logic [17:0] odd_increment;
    wire [32:0] next_square={1'b0,sqr_o}+33'(odd_increment);
    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin sqr_o<=1; odd_increment<=3; end
        else if (sqr_o!=32'hffffffff) begin
            sqr_o<=next_square[32] ? 32'hffffffff : next_square[31:0];
            odd_increment<=odd_increment+18'd2;
        end
    end
endmodule
