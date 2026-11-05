module HexlMontgomeryReduce64 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [63:0] in_T_hi,
    input logic [63:0] in_T_lo,
    input logic [63:0] in_q,
    input logic [6:0] in_r,
    input logic [63:0] in_mod_R_msk,
    input logic [63:0] in_inv_mod,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_S,
    output logic out_error
);
    logic pending;
    logic [63:0] out_S_next;
    logic out_error_next;
    assign in_ready = !pending || out_ready;
    assign out_valid = pending && !rst;
    logic [127:0] t, radix, inverse_product, m, u;

    always_comb begin
        t = {in_T_hi, in_T_lo};
        radix = 128'd1 << in_r;
        inverse_product = 128'(in_q) * in_inv_mod;
        m = (128'(in_T_lo) * in_inv_mod) & in_mod_R_msk;
        u = (t + m * in_q) >> in_r;
        out_error_next = in_r < 1 || in_r > 63 || in_q == 0 ||
            in_q >= radix || !in_q[0] || 128'(in_mod_R_msk) != radix-1 ||
            in_inv_mod >= radix || (inverse_product & in_mod_R_msk) != in_mod_R_msk ||
            t >= radix * in_q;
        out_S_next = out_error_next ? 64'd0 : 64'(u >= in_q ? u-in_q : u);
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            pending <= 1'b0;
            out_S <= '0;
            out_error <= '0;
        end else if (in_ready) begin
            pending <= in_valid;
            if (in_valid) begin
                out_S <= out_S_next;
                out_error <= out_error_next;
            end
        end
    end
endmodule
