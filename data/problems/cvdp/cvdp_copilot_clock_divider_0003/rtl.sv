module clock_divider (
    input logic clk, input logic rst_n, input logic [1:0] sel,
    output logic clk_out
);
    logic [2:0] count;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) count <= 0;
        else count <= count + 1'b1;
    end
    always_comb begin
        case (sel)
            0: clk_out = count[0];
            1: clk_out = count[1];
            2: clk_out = count[2];
            default: clk_out = 0;
        endcase
    end
endmodule
