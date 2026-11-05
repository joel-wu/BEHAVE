module ai_ml_tx_norm_ln_groupnorm_n_groups (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_x,
    input  logic [127:0] in_gamma,
    input  logic [127:0] in_beta,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [127:0] out_y
);

    logic         busy;
    logic [127:0] out_y_reg;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;
    assign out_y     = out_y_reg;

    function automatic logic signed [31:0] trunc_div4_tz(input logic signed [31:0] n);
        logic signed [31:0] abs_n;
        logic signed [31:0] q;
        begin
            if (n < 0)
                abs_n = -n;
            else
                abs_n = n;
            q = abs_n >>> 2;
            if (n < 0)
                trunc_div4_tz = -q;
            else
                trunc_div4_tz = q;
        end
    endfunction

    function automatic logic signed [63:0] trunc_div256_tz(input logic signed [63:0] n);
        logic signed [63:0] abs_n;
        logic signed [63:0] q;
        begin
            if (n < 0)
                abs_n = -n;
            else
                abs_n = n;
            q = abs_n >>> 8;
            if (n < 0)
                trunc_div256_tz = -q;
            else
                trunc_div256_tz = q;
        end
    endfunction

    function automatic logic signed [63:0] trunc_div_by_u_tz(
        input logic signed [63:0] n,
        input logic [31:0] d
    );
        logic signed [63:0] abs_n;
        logic [63:0] q_unsigned;
        begin
            if (n < 0)
                abs_n = -n;
            else
                abs_n = n;
            q_unsigned = abs_n / d;
            if (n < 0)
                trunc_div_by_u_tz = -$signed(q_unsigned);
            else
                trunc_div_by_u_tz = $signed(q_unsigned);
        end
    endfunction

    function automatic logic [31:0] isqrt_floor32_constiters(input logic [31:0] n_in);
        logic [31:0] n;
        logic [31:0] x;
        logic [31:0] bitv;
        integer i;
        begin
            n = n_in;
            x = 32'd0;
            bitv = 32'h4000_0000;

            for (i = 0; i < 16; i = i + 1) begin
                if (bitv > n)
                    bitv = bitv >> 2;
            end

            for (i = 0; i < 16; i = i + 1) begin
                if (bitv != 0) begin
                    if (n >= x + bitv) begin
                        n = n - (x + bitv);
                        x = (x >> 1) + bitv;
                    end else begin
                        x = x >> 1;
                    end
                    bitv = bitv >> 2;
                end
            end

            isqrt_floor32_constiters = x;
        end
    endfunction

    logic [127:0] computed_out_y;

    logic signed [15:0] xs     [0:7];
    logic signed [15:0] gammas [0:7];
    logic signed [15:0] betas  [0:7];

    logic signed [31:0] sum_g0, sum_g1;
    logic signed [31:0] mean_g0, mean_g1;

    logic signed [31:0] centered [0:7];

    logic signed [63:0] sq0, sq1, sq2, sq3, sq4, sq5, sq6, sq7;
    logic signed [63:0] sqsum_g0, sqsum_g1;
    logic signed [31:0] var_g0, var_g1;
    logic [31:0] den_g0, den_g1;

    logic signed [63:0] norm_num [0:7];
    logic signed [63:0] norm_i   [0:7];
    logic signed [63:0] prod_c   [0:7];
    logic signed [63:0] scaled_c [0:7];
    logic signed [63:0] affine_c [0:7];
    logic signed [63:0] y_pre_sat_c [0:7];

    logic signed [31:0] x0_32, x1_32, x2_32, x3_32, x4_32, x5_32, x6_32, x7_32;
    logic signed [63:0] beta0_64, beta1_64, beta2_64, beta3_64, beta4_64, beta5_64, beta6_64, beta7_64;

    integer k;
    always_comb begin
        for (k = 0; k < 8; k = k + 1) begin
            xs[k]          = in_x[k*16 +: 16];
            gammas[k]      = in_gamma[k*16 +: 16];
            betas[k]       = in_beta[k*16 +: 16];
            centered[k]    = 32'sd0;
            norm_num[k]    = 64'sd0;
            norm_i[k]      = 64'sd0;
            prod_c[k]      = 64'sd0;
            scaled_c[k]    = 64'sd0;
            affine_c[k]    = 64'sd0;
            y_pre_sat_c[k] = 64'sd0;
        end

        x0_32 = xs[0];
        x1_32 = xs[1];
        x2_32 = xs[2];
        x3_32 = xs[3];
        x4_32 = xs[4];
        x5_32 = xs[5];
        x6_32 = xs[6];
        x7_32 = xs[7];

        beta0_64 = betas[0];
        beta1_64 = betas[1];
        beta2_64 = betas[2];
        beta3_64 = betas[3];
        beta4_64 = betas[4];
        beta5_64 = betas[5];
        beta6_64 = betas[6];
        beta7_64 = betas[7];

        sum_g0  = x0_32 + x1_32 + x2_32 + x3_32;
        sum_g1  = x4_32 + x5_32 + x6_32 + x7_32;

        mean_g0 = trunc_div4_tz(sum_g0);
        mean_g1 = trunc_div4_tz(sum_g1);

        centered[0] = x0_32 - mean_g0;
        centered[1] = x1_32 - mean_g0;
        centered[2] = x2_32 - mean_g0;
        centered[3] = x3_32 - mean_g0;
        centered[4] = x4_32 - mean_g1;
        centered[5] = x5_32 - mean_g1;
        centered[6] = x6_32 - mean_g1;
        centered[7] = x7_32 - mean_g1;

        sq0 = centered[0] * centered[0];
        sq1 = centered[1] * centered[1];
        sq2 = centered[2] * centered[2];
        sq3 = centered[3] * centered[3];
        sq4 = centered[4] * centered[4];
        sq5 = centered[5] * centered[5];
        sq6 = centered[6] * centered[6];
        sq7 = centered[7] * centered[7];

        sqsum_g0 = sq0 + sq1 + sq2 + sq3;
        sqsum_g1 = sq4 + sq5 + sq6 + sq7;

        var_g0 = sqsum_g0[31:0] >>> 2;
        var_g1 = sqsum_g1[31:0] >>> 2;

        den_g0 = isqrt_floor32_constiters(var_g0[31:0] + 32'd1);
        den_g1 = isqrt_floor32_constiters(var_g1[31:0] + 32'd1);

        norm_num[0] = centered[0] * 64'sd256;
        norm_num[1] = centered[1] * 64'sd256;
        norm_num[2] = centered[2] * 64'sd256;
        norm_num[3] = centered[3] * 64'sd256;
        norm_num[4] = centered[4] * 64'sd256;
        norm_num[5] = centered[5] * 64'sd256;
        norm_num[6] = centered[6] * 64'sd256;
        norm_num[7] = centered[7] * 64'sd256;

        norm_i[0] = trunc_div_by_u_tz(norm_num[0], den_g0);
        norm_i[1] = trunc_div_by_u_tz(norm_num[1], den_g0);
        norm_i[2] = trunc_div_by_u_tz(norm_num[2], den_g0);
        norm_i[3] = trunc_div_by_u_tz(norm_num[3], den_g0);
        norm_i[4] = trunc_div_by_u_tz(norm_num[4], den_g1);
        norm_i[5] = trunc_div_by_u_tz(norm_num[5], den_g1);
        norm_i[6] = trunc_div_by_u_tz(norm_num[6], den_g1);
        norm_i[7] = trunc_div_by_u_tz(norm_num[7], den_g1);

        prod_c[0] = gammas[0] * norm_i[0];
        prod_c[1] = gammas[1] * norm_i[1];
        prod_c[2] = gammas[2] * norm_i[2];
        prod_c[3] = gammas[3] * norm_i[3];
        prod_c[4] = gammas[4] * norm_i[4];
        prod_c[5] = gammas[5] * norm_i[5];
        prod_c[6] = gammas[6] * norm_i[6];
        prod_c[7] = gammas[7] * norm_i[7];

        scaled_c[0] = trunc_div256_tz(prod_c[0]);
        scaled_c[1] = trunc_div256_tz(prod_c[1]);
        scaled_c[2] = trunc_div256_tz(prod_c[2]);
        scaled_c[3] = trunc_div256_tz(prod_c[3]);
        scaled_c[4] = trunc_div256_tz(prod_c[4]);
        scaled_c[5] = trunc_div256_tz(prod_c[5]);
        scaled_c[6] = trunc_div256_tz(prod_c[6]);
        scaled_c[7] = trunc_div256_tz(prod_c[7]);

        affine_c[0] = scaled_c[0] + beta0_64;
        affine_c[1] = scaled_c[1] + beta1_64;
        affine_c[2] = scaled_c[2] + beta2_64;
        affine_c[3] = scaled_c[3] + beta3_64;
        affine_c[4] = scaled_c[4] + beta4_64;
        affine_c[5] = scaled_c[5] + beta5_64;
        affine_c[6] = scaled_c[6] + beta6_64;
        affine_c[7] = scaled_c[7] + beta7_64;

        y_pre_sat_c[0] = trunc_div256_tz(affine_c[0]);
        y_pre_sat_c[1] = trunc_div256_tz(affine_c[1]);
        y_pre_sat_c[2] = trunc_div256_tz(affine_c[2]);
        y_pre_sat_c[3] = trunc_div256_tz(affine_c[3]);
        y_pre_sat_c[4] = trunc_div256_tz(affine_c[4]);
        y_pre_sat_c[5] = trunc_div256_tz(affine_c[5]);
        y_pre_sat_c[6] = trunc_div256_tz(affine_c[6]);
        y_pre_sat_c[7] = trunc_div256_tz(affine_c[7]);

        computed_out_y = 128'd0;
        computed_out_y[ 15:  0] = y_pre_sat_c[0][15:0];
        computed_out_y[ 31: 16] = y_pre_sat_c[1][15:0];
        computed_out_y[ 47: 32] = y_pre_sat_c[2][15:0];
        computed_out_y[ 63: 48] = y_pre_sat_c[3][15:0];
        computed_out_y[ 79: 64] = y_pre_sat_c[4][15:0];
        computed_out_y[ 95: 80] = y_pre_sat_c[5][15:0];
        computed_out_y[111: 96] = y_pre_sat_c[6][15:0];
        computed_out_y[127:112] = y_pre_sat_c[7][15:0];
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy      <= 1'b0;
            out_y_reg <= 128'd0;
        end else if (input_accepted) begin
            busy      <= 1'b1;
            out_y_reg <= computed_out_y;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule