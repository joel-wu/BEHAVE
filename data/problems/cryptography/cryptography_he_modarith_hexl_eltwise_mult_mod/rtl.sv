module HexlScalarMultiplyMod (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [63:0] in_x,
    input logic [63:0] in_y_operand,
    input logic [63:0] in_y_barrett_factor,
    input logic [63:0] in_modulus,
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
    logic [127:0] xf, xy, fq, upper, target, remainder;

    always_comb begin
        xf = 128'(in_x) * in_y_barrett_factor;
        xy = 128'(in_x) * in_y_operand;
        fq = 128'(in_y_barrett_factor) * in_modulus;
        upper = (128'(in_y_barrett_factor) + 128'd1) * in_modulus;
        target = {in_y_operand, 64'd0};
        remainder = xy - 128'(xf[127:64]) * in_modulus;
        out_error_next = in_modulus < 2 || in_modulus[63] ||
            in_y_operand >= in_modulus || fq > target || upper <= target;
        out_value_next = out_error_next ? 64'd0 :
            64'(remainder >= in_modulus ? remainder - in_modulus : remainder);
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
