module multi_pipe_4bit #(
    parameter integer size = 4
) (
    input logic clk,
    input logic rst_n,
    input logic [size-1:0] mul_a,
    input logic [size-1:0] mul_b,
    output logic [2*size-1:0] mul_out
);
    logic [2*size-1:0] product;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            product <= '0;
            mul_out <= '0;
        end else begin
            product <= mul_a * mul_b;
            mul_out <= product;
        end
    end
endmodule
