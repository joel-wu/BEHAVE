module cryptography_pqc_kyber_kyber_basemul (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_a0,
    input  logic [15:0]  in_a1,
    input  logic [15:0]  in_b0,
    input  logic [15:0]  in_b1,
    input  logic [15:0]  in_zeta,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_r0,
    output logic [15:0]  out_r1
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic signed [15:0] montgomery_reduce(
        input logic signed [31:0] x
    );
        logic [47:0] mult_full;
        logic [15:0] u_bits;
        logic signed [32:0] uq_ext;
        logic signed [32:0] x_ext;
        logic signed [32:0] t;
    begin
        montgomery_reduce = 16'sd0;
        mult_full = x * 32'sd62209;
        u_bits = mult_full[15:0];
        uq_ext = $signed(u_bits) * 33'sd3329;
        x_ext = {x[31], x};
        t = x_ext - uq_ext;
        montgomery_reduce = t[32:16];
    end
    endfunction

    logic signed [15:0] a0_s;
    logic signed [15:0] a1_s;
    logic signed [15:0] b0_s;
    logic signed [15:0] b1_s;
    logic signed [15:0] zeta_s;

    logic signed [15:0] p0;
    logic signed [15:0] p1;
    logic signed [15:0] p2;
    logic signed [15:0] p3;
    logic signed [15:0] p4;
    logic signed [16:0] r0_sum;
    logic signed [16:0] r1_sum;

    logic signed [31:0] mul_a1_b1;
    logic signed [31:0] mul_p0_zeta;
    logic signed [31:0] mul_a0_b0;
    logic signed [31:0] mul_a0_b1;
    logic signed [31:0] mul_a1_b0;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy   <= 1'b0;
            out_r0 <= 16'h0000;
            out_r1 <= 16'h0000;
        end else if (input_accepted) begin
            a0_s   = $signed(in_a0);
            a1_s   = $signed(in_a1);
            b0_s   = $signed(in_b0);
            b1_s   = $signed(in_b1);
            zeta_s = $signed(in_zeta);

            mul_a1_b1 = a1_s * b1_s;
            p0 = montgomery_reduce(mul_a1_b1);

            mul_p0_zeta = p0 * zeta_s;
            p1 = montgomery_reduce(mul_p0_zeta);

            mul_a0_b0 = a0_s * b0_s;
            p2 = montgomery_reduce(mul_a0_b0);

            r0_sum = p1 + p2;

            mul_a0_b1 = a0_s * b1_s;
            p3 = montgomery_reduce(mul_a0_b1);

            mul_a1_b0 = a1_s * b0_s;
            p4 = montgomery_reduce(mul_a1_b0);

            r1_sum = p3 + p4;

            out_r0 <= r0_sum[15:0];
            out_r1 <= r1_sum[15:0];
            busy   <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
