module cryptography_pk_classic_rsa_mont_mul_limb #(
    parameter int unsigned NW = 4
) (
    input  logic                  clk,
    input  logic                  rst,

    input  logic                  in_valid,
    output logic                  in_ready,
    input  logic [64*NW-1:0]      in_a,
    input  logic [64*NW-1:0]      in_b,
    input  logic [64*NW-1:0]      in_n,
    input  logic [63:0]           in_m0,

    output logic                  out_valid,
    input  logic                  out_ready,
    output logic [64*NW-1:0]      out_result
);

    localparam int unsigned TW = 64 * NW;
    localparam int unsigned TARR_W = 64 * (2 * NW + 1);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    logic [TARR_W-1:0] p_vec_c;
    logic [TARR_W-1:0] t_vec_c;
    logic [TW-1:0]     u_vec_c;
    logic [TW-1:0]     r_vec_c;
    logic [TW-1:0]     result_c;
    logic              u_ge_n_c;

    integer i, j, idx;

    logic [63:0] a_limb;
    logic [63:0] b_limb;
    logic [63:0] n_limb;
    logic [63:0] p_limb;
    logic [63:0] t_limb;
    logic [63:0] u_limb;
    logic [63:0] res_limb;
    logic [127:0] acc128;
    logic [127:0] mul128;
    logic [63:0] carry64;
    logic [63:0] borrow64;
    logic [63:0] k64;

    always_comb begin
        p_vec_c   = '0;
        t_vec_c   = '0;
        u_vec_c   = '0;
        r_vec_c   = '0;
        result_c  = '0;
        u_ge_n_c  = 1'b1;

        a_limb   = '0;
        b_limb   = '0;
        n_limb   = '0;
        p_limb   = '0;
        t_limb   = '0;
        u_limb   = '0;
        res_limb = '0;
        acc128   = '0;
        mul128   = '0;
        carry64  = '0;
        borrow64 = '0;
        k64      = '0;
        idx      = 0;

        for (i = 0; i < NW; i = i + 1) begin
            carry64 = 64'd0;
            a_limb = in_a[i*64 +: 64];
            for (j = 0; j < NW; j = j + 1) begin
                b_limb = in_b[j*64 +: 64];
                p_limb = p_vec_c[(i+j)*64 +: 64];
                mul128 = a_limb * b_limb;
                acc128 = {64'd0, p_limb} + mul128 + {64'd0, carry64};
                p_vec_c[(i+j)*64 +: 64] = acc128[63:0];
                carry64 = acc128[127:64];
            end
            p_vec_c[(i+NW)*64 +: 64] = carry64;
        end

        for (i = 0; i < 2*NW; i = i + 1) begin
            t_vec_c[i*64 +: 64] = p_vec_c[i*64 +: 64];
        end
        t_vec_c[(2*NW)*64 +: 64] = 64'd0;

        for (i = 0; i < NW; i = i + 1) begin
            t_limb = t_vec_c[i*64 +: 64];
            k64 = (t_limb * in_m0) & 64'hFFFF_FFFF_FFFF_FFFF;
            carry64 = 64'd0;
            for (j = 0; j < NW; j = j + 1) begin
                n_limb = in_n[j*64 +: 64];
                t_limb = t_vec_c[(i+j)*64 +: 64];
                mul128 = k64 * n_limb;
                acc128 = {64'd0, t_limb} + mul128 + {64'd0, carry64};
                t_vec_c[(i+j)*64 +: 64] = acc128[63:0];
                carry64 = acc128[127:64];
            end
            for (idx = 0; idx <= 2*NW; idx = idx + 1) begin
                if (idx >= i + NW && carry64 != 64'd0) begin
                    t_limb = t_vec_c[idx*64 +: 64];
                    acc128 = {64'd0, t_limb} + {64'd0, carry64};
                    t_vec_c[idx*64 +: 64] = acc128[63:0];
                    carry64 = acc128[127:64];
                end
            end
        end

        for (j = 0; j < NW; j = j + 1) begin
            u_vec_c[j*64 +: 64] = t_vec_c[(NW+j)*64 +: 64];
        end

        u_ge_n_c = (u_vec_c >= in_n);

        if ((t_vec_c[(2*NW)*64 +: 64] != 64'd0) || (u_ge_n_c == 1'b1)) begin
            borrow64 = 64'd0;
            for (j = 0; j < NW; j = j + 1) begin
                u_limb = u_vec_c[j*64 +: 64];
                n_limb = in_n[j*64 +: 64];
                if (borrow64 == 64'd0) begin
                    if (u_limb < n_limb) begin
                        res_limb = u_limb - n_limb;
                        borrow64 = 64'd1;
                    end else begin
                        res_limb = u_limb - n_limb;
                        borrow64 = 64'd0;
                    end
                end else begin
                    if (u_limb <= n_limb) begin
                        res_limb = u_limb - n_limb - 64'd1;
                        borrow64 = 64'd1;
                    end else begin
                        res_limb = u_limb - n_limb - 64'd1;
                        borrow64 = 64'd0;
                    end
                end
                r_vec_c[j*64 +: 64] = res_limb;
            end
            result_c = r_vec_c;
        end else begin
            result_c = u_vec_c;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy       <= 1'b0;
            out_result <= '0;
        end else if (input_accepted) begin
            out_result <= result_c;
            busy       <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
