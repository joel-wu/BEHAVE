module cryptography_pqc_kyber_kyber_ntt_butterfly (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_a0,
    input  logic [15:0] in_a1,
    input  logic [15:0] in_b0,
    input  logic [15:0] in_b1,
    input  logic [15:0] in_zeta,

    output logic        out_valid,
    output logic out_error,
    input  logic        out_ready,
    output logic [15:0] out_r0,
    output logic [15:0] out_r1
);

    logic busy;
    wire invalid_input = ($signed(in_a0) <= -16'sd3329 || $signed(in_a0) >= 16'sd3329) ||
        ($signed(in_a1) <= -16'sd3329 || $signed(in_a1) >= 16'sd3329) ||
        ($signed(in_b0) <= -16'sd3329 || $signed(in_b0) >= 16'sd3329) ||
        ($signed(in_b1) <= -16'sd3329 || $signed(in_b1) >= 16'sd3329) ||
        ($signed(in_zeta) <= -16'sd3329 || $signed(in_zeta) >= 16'sd3329);
    wire input_accepted;
    wire output_consumed;

    function automatic logic signed [15:0] montgomery_reduce(
        input logic signed [31:0] x
    );
        logic signed [15:0] x_low_signed;
        logic signed [15:0] qinv_signed;
        logic signed [15:0] m_signed;
        logic signed [31:0] mult32;
        logic signed [31:0] t32;
        begin
            x_low_signed = x[15:0];
            qinv_signed  = -16'sd3327;
            mult32       = x_low_signed * qinv_signed;
            m_signed        = mult32[15:0];
            t32          = x - $signed(m_signed) * 32'sd3329;
            montgomery_reduce = t32 >>> 16;
        end
    endfunction

    function automatic logic [15:0] butterfly_r0(
        input logic [15:0] a0_u,
        input logic [15:0] a1_u,
        input logic [15:0] b0_u,
        input logic [15:0] b1_u,
        input logic [15:0] zeta_u
    );
        logic signed [15:0] a0_s;
        logic signed [15:0] a1_s;
        logic signed [15:0] b0_s;
        logic signed [15:0] b1_s;
        logic signed [15:0] zeta_s;
        logic signed [31:0] prod32;
        logic signed [15:0] p0;
        logic signed [15:0] p1;
        logic signed [15:0] p2;
        logic signed [16:0] r0_s;
        begin
            a0_s = a0_u;
            a1_s = a1_u;
            b0_s = b0_u;
            b1_s = b1_u;
            zeta_s = zeta_u;

            prod32 = a1_s * b1_s;
            p0 = montgomery_reduce(prod32);
            prod32 = p0 * zeta_s;
            p1 = montgomery_reduce(prod32);
            prod32 = a0_s * b0_s;
            p2 = montgomery_reduce(prod32);
            r0_s = p1 + p2;

            butterfly_r0 = r0_s[15:0];
        end
    endfunction

    function automatic logic [15:0] butterfly_r1(
        input logic [15:0] a0_u,
        input logic [15:0] a1_u,
        input logic [15:0] b0_u,
        input logic [15:0] b1_u
    );
        logic signed [15:0] a0_s;
        logic signed [15:0] a1_s;
        logic signed [15:0] b0_s;
        logic signed [15:0] b1_s;
        logic signed [31:0] prod32;
        logic signed [15:0] p3;
        logic signed [15:0] p4;
        logic signed [16:0] r1_s;
        begin
            a0_s = a0_u;
            a1_s = a1_u;
            b0_s = b0_u;
            b1_s = b1_u;

            prod32 = a0_s * b1_s;
            p3 = montgomery_reduce(prod32);
            prod32 = a1_s * b0_s;
            p4 = montgomery_reduce(prod32);
            r1_s = p3 + p4;

            butterfly_r1 = r1_s[15:0];
        end
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy   <= 1'b0;
            out_error <= 1'b0;
            out_r0 <= 16'h0000;
            out_r1 <= 16'h0000;
        end else if (input_accepted) begin
            out_r0 <= invalid_input ? '0 : (butterfly_r0(in_a0, in_a1, in_b0, in_b1, in_zeta));
            out_r1 <= invalid_input ? '0 : (butterfly_r1(in_a0, in_a1, in_b0, in_b1));
            busy   <= 1'b1;
            out_error <= invalid_input;
        end else if (output_consumed) begin
            busy   <= 1'b0;
            out_error <= 1'b0;
            out_r0 <= 16'h0000;
            out_r1 <= 16'h0000;
        end
    end

endmodule
