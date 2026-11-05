module cryptography_he_ntt_hexl_harvey_fwd_butterfly (
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

logic [63:0]  twice_modulus_v;
logic [63:0]  tx_v;
logic [127:0] product_v;
logic [127:0] precon_product_v;
logic [63:0]  q_hat_v;
logic [127:0] q_hat_times_q_v;
logic [127:0] candidate_v;
logic [127:0] t_v;
logic [127:0] tx128_v;
logic [127:0] twice_modulus128_v;
logic [127:0] x_r_full_v;
logic [127:0] y_r_full_v;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    twice_modulus_v = in_modulus << 1;

    if (in_x_op >= twice_modulus_v)
        tx_v = in_x_op - twice_modulus_v;
    else
        tx_v = in_x_op;

    product_v        = {64'd0, in_y_op} * {64'd0, in_w};
    precon_product_v = {64'd0, in_y_op} * {64'd0, in_w_precon};
    q_hat_v          = precon_product_v[127:64];
    q_hat_times_q_v  = {64'd0, q_hat_v} * {64'd0, in_modulus};
    candidate_v      = product_v - q_hat_times_q_v;

    t_v = {64'd0, candidate_v[63:0]};

    tx128_v            = {64'd0, tx_v};
    twice_modulus128_v = {64'd0, twice_modulus_v};
    x_r_full_v         = tx128_v + t_v;
    y_r_full_v         = tx128_v + twice_modulus128_v - t_v;
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy    <= 1'b0;
        out_x_r <= 64'd0;
        out_y_r <= 64'd0;
    end else if (input_accepted) begin
        busy    <= 1'b1;
        out_x_r <= x_r_full_v[63:0];
        out_y_r <= y_r_full_v[63:0];
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
