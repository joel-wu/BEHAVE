module cryptography_he_ntt_hexl_harvey_inv_butterfly (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_x_op,
    input  logic [63:0]  in_y_op,
    input  logic [63:0]  in_w,
    input  logic [63:0]  in_w_precon,
    input  logic [63:0]  in_modulus,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_x_r,
    output logic [63:0]  out_y_r
);

    logic busy;

    wire input_accepted;
    wire output_consumed;

    logic [63:0] x_r_next;
    logic [63:0] y_r_next;

    logic [63:0] two_q_64;
    logic [63:0] diff_64;

    logic [127:0] prod_128;
    logic [127:0] precon_prod_128;
    logic [63:0]  q_hat_64;
    logic [127:0] qhat_mul_q_128;
    logic [127:0] candidate_128;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        two_q_64 = in_modulus << 1;
        x_r_next = in_x_op + in_y_op;
        if (x_r_next >= two_q_64) x_r_next = x_r_next - two_q_64;
        diff_64 = in_x_op + two_q_64 - in_y_op;
        prod_128 = diff_64 * in_w;
        precon_prod_128 = diff_64 * in_w_precon;
        q_hat_64 = precon_prod_128[127:64];
        qhat_mul_q_128 = q_hat_64 * in_modulus;
        candidate_128 = prod_128 - qhat_mul_q_128;
        y_r_next = candidate_128[63:0];
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy    <= 1'b0;
            out_x_r <= 64'd0;
            out_y_r <= 64'd0;
        end else if (input_accepted) begin
            busy    <= 1'b1;
            out_x_r <= x_r_next;
            out_y_r <= y_r_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
