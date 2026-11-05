module HexlBarrettReduce64 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [63:0] in_input,
    input logic [63:0] in_modulus,
    input logic [63:0] in_q_barr,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_value,
    output logic out_error
);
    logic pending;
    logic [63:0] out_value_next;
    logic out_error_next;
    assign in_ready = !pending || out_ready;
    assign out_valid = pending && !rst;
    logic [127:0] product, reciprocal_product, upper_product;
    logic [63:0] quotient;

    always_comb begin
        product = 128'(in_input) * in_q_barr;
        reciprocal_product = 128'(in_q_barr) * in_modulus;
        upper_product = (128'(in_q_barr) + 128'd1) * in_modulus;
        quotient = product[127:64];
        out_error_next = in_modulus < 2 ||
            reciprocal_product > (128'd1 << 64) || upper_product <= (128'd1 << 64);
        out_value_next = out_error_next ? 64'd0 : in_input - quotient * in_modulus;
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            pending <= 1'b0;
            out_value <= '0;
            out_error <= '0;
        end else if (in_ready) begin
            pending <= in_valid;
            if (in_valid) begin
                out_value <= out_value_next;
                out_error <= out_error_next;
            end
        end
    end
endmodule
