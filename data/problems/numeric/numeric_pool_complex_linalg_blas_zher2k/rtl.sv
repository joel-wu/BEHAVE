module hermitian_rank2k_tile (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_start,
    input  logic         in_last,
    input  logic         in_uplo,
    input  logic         in_trans,
    input  logic signed [31:0] in_alpha_re,
    input  logic signed [31:0] in_alpha_im,
    input  logic signed [31:0] in_beta,
    input  logic signed [31:0] in_c00_re,
    input  logic signed [31:0] in_c00_im,
    input  logic signed [31:0] in_c01_re,
    input  logic signed [31:0] in_c01_im,
    input  logic signed [31:0] in_c10_re,
    input  logic signed [31:0] in_c10_im,
    input  logic signed [31:0] in_c11_re,
    input  logic signed [31:0] in_c11_im,
    input  logic signed [31:0] in_a0_re,
    input  logic signed [31:0] in_a0_im,
    input  logic signed [31:0] in_a1_re,
    input  logic signed [31:0] in_a1_im,
    input  logic signed [31:0] in_b0_re,
    input  logic signed [31:0] in_b0_im,
    input  logic signed [31:0] in_b1_re,
    input  logic signed [31:0] in_b1_im,

    output logic         out_valid,
    input  logic         out_ready,
    output logic         out_uplo,
    output logic signed [31:0] out_c00_re,
    output logic signed [31:0] out_c00_im,
    output logic signed [31:0] out_c01_re,
    output logic signed [31:0] out_c01_im,
    output logic signed [31:0] out_c10_re,
    output logic signed [31:0] out_c10_im,
    output logic signed [31:0] out_c11_re,
    output logic signed [31:0] out_c11_im
);

logic busy;
wire  input_accepted;
wire  output_consumed;

logic uplo_reg;
logic trans_reg;
logic signed [31:0] alpha_re_reg;
logic signed [31:0] alpha_im_reg;
logic signed [31:0] alpha_conj_re_reg;
logic signed [31:0] alpha_conj_im_reg;
logic signed [31:0] beta_reg;

logic signed [255:0] c00_re_reg;
logic signed [255:0] c01_re_reg;
logic signed [255:0] c01_im_reg;
logic signed [255:0] c10_re_reg;
logic signed [255:0] c10_im_reg;
logic signed [255:0] c11_re_reg;

logic out_uplo_reg;
logic signed [31:0] out_c00_re_reg;
logic signed [31:0] out_c00_im_reg;
logic signed [31:0] out_c01_re_reg;
logic signed [31:0] out_c01_im_reg;
logic signed [31:0] out_c10_re_reg;
logic signed [31:0] out_c10_im_reg;
logic signed [31:0] out_c11_re_reg;
logic signed [31:0] out_c11_im_reg;

logic signed [255:0] neg_c01_im_tmp;
logic signed [255:0] neg_c10_im_tmp;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

assign out_uplo   = out_uplo_reg;
assign out_c00_re = out_c00_re_reg;
assign out_c00_im = out_c00_im_reg;
assign out_c01_re = out_c01_re_reg;
assign out_c01_im = out_c01_im_reg;
assign out_c10_re = out_c10_re_reg;
assign out_c10_im = out_c10_im_reg;
assign out_c11_re = out_c11_re_reg;
assign out_c11_im = out_c11_im_reg;

always_ff @(posedge clk) begin
    logic                   next_uplo;
    logic                   next_trans;
    logic signed [31:0]     next_alpha_re;
    logic signed [31:0]     next_alpha_im;
    logic signed [31:0]     next_alpha_conj_re;
    logic signed [31:0]     next_alpha_conj_im;
    logic signed [31:0]     next_beta;

    logic signed [255:0]    next_c00_re;
    logic signed [255:0]    next_c01_re;
    logic signed [255:0]    next_c01_im;
    logic signed [255:0]    next_c10_re;
    logic signed [255:0]    next_c10_im;
    logic signed [255:0]    next_c11_re;

    logic signed [31:0]     a0_re;
    logic signed [31:0]     a0_im;
    logic signed [31:0]     a1_re;
    logic signed [31:0]     a1_im;
    logic signed [31:0]     b0_re;
    logic signed [31:0]     b0_im;
    logic signed [31:0]     b1_re;
    logic signed [31:0]     b1_im;

    logic signed [63:0]     temp1_0_re;
    logic signed [63:0]     temp1_0_im;
    logic signed [63:0]     temp2_0_re;
    logic signed [63:0]     temp2_0_im;
    logic signed [127:0]    diag0_re;
    logic signed [127:0]    add01_re_t0;
    logic signed [127:0]    add01_im_t0;

    logic signed [63:0]     temp1_1_re;
    logic signed [63:0]     temp1_1_im;
    logic signed [63:0]     temp2_1_re;
    logic signed [63:0]     temp2_1_im;
    logic signed [127:0]    diag1_re;
    logic signed [127:0]    add10_re_t0;
    logic signed [127:0]    add10_im_t0;

    logic signed [63:0]     t00_0_re;
    logic signed [63:0]     t00_0_im;
    logic signed [63:0]     u00_0_re;
    logic signed [63:0]     u00_0_im;
    logic signed [95:0]     add00_re_t1;

    logic signed [63:0]     t11_0_re;
    logic signed [63:0]     t11_0_im;
    logic signed [63:0]     u11_0_re;
    logic signed [63:0]     u11_0_im;
    logic signed [95:0]     add11_re_t1;

    logic signed [63:0]     t01_re;
    logic signed [63:0]     t01_im;
    logic signed [63:0]     u01_re;
    logic signed [63:0]     u01_im;
    logic signed [95:0]     add01_re_t1;
    logic signed [95:0]     add01_im_t1;

    logic signed [63:0]     t10_re;
    logic signed [63:0]     t10_im;
    logic signed [63:0]     u10_re;
    logic signed [63:0]     u10_im;
    logic signed [95:0]     add10_re_t1;
    logic signed [95:0]     add10_im_t1;

    logic signed [255:0]    neg_next_c01_im;
    logic signed [255:0]    neg_next_c10_im;

    if (rst) begin
        busy <= 1'b0;

        uplo_reg <= 1'b0;
        trans_reg <= 1'b0;
        alpha_re_reg <= 32'sd0;
        alpha_im_reg <= 32'sd0;
        alpha_conj_re_reg <= 32'sd0;
        alpha_conj_im_reg <= 32'sd0;
        beta_reg <= 32'sd0;

        c00_re_reg <= 256'sd0;
        c01_re_reg <= 256'sd0;
        c01_im_reg <= 256'sd0;
        c10_re_reg <= 256'sd0;
        c10_im_reg <= 256'sd0;
        c11_re_reg <= 256'sd0;

        out_uplo_reg <= 1'b0;
        out_c00_re_reg <= 32'sd0;
        out_c00_im_reg <= 32'sd0;
        out_c01_re_reg <= 32'sd0;
        out_c01_im_reg <= 32'sd0;
        out_c10_re_reg <= 32'sd0;
        out_c10_im_reg <= 32'sd0;
        out_c11_re_reg <= 32'sd0;
        out_c11_im_reg <= 32'sd0;
    end else if (input_accepted) begin
        next_uplo = uplo_reg;
        next_trans = trans_reg;
        next_alpha_re = alpha_re_reg;
        next_alpha_im = alpha_im_reg;
        next_alpha_conj_re = alpha_conj_re_reg;
        next_alpha_conj_im = alpha_conj_im_reg;
        next_beta = beta_reg;

        next_c00_re = c00_re_reg;
        next_c01_re = c01_re_reg;
        next_c01_im = c01_im_reg;
        next_c10_re = c10_re_reg;
        next_c10_im = c10_im_reg;
        next_c11_re = c11_re_reg;

        a0_re = in_a0_re;
        a0_im = in_a0_im;
        a1_re = in_a1_re;
        a1_im = in_a1_im;
        b0_re = in_b0_re;
        b0_im = in_b0_im;
        b1_re = in_b1_re;
        b1_im = in_b1_im;

        if (in_start) begin
            next_uplo = in_uplo;
            next_trans = in_trans;
            next_alpha_re = in_alpha_re;
            next_alpha_im = in_alpha_im;
            next_alpha_conj_re = in_alpha_re;
            next_alpha_conj_im = -in_alpha_im;
            next_beta = in_beta;

            next_c00_re = $signed(in_c00_re);
            next_c01_re = $signed(in_c01_re);
            next_c01_im = $signed(in_c01_im);
            next_c10_re = $signed(in_c10_re);
            next_c10_im = $signed(in_c10_im);
            next_c11_re = $signed(in_c11_re);

            if (in_uplo) begin
                if (in_beta == 32'sd0) begin
                    next_c00_re = 256'sd0;
                    next_c01_re = 256'sd0;
                    next_c01_im = 256'sd0;
                    next_c11_re = 256'sd0;
                end else if (in_beta != 32'sd1) begin
                    next_c00_re = $signed(in_beta) * $signed(in_c00_re);
                    next_c01_re = $signed(in_beta) * $signed(in_c01_re);
                    next_c01_im = $signed(in_beta) * $signed(in_c01_im);
                    next_c11_re = $signed(in_beta) * $signed(in_c11_re);
                end
            end else begin
                if (in_beta == 32'sd0) begin
                    next_c00_re = 256'sd0;
                    next_c10_re = 256'sd0;
                    next_c10_im = 256'sd0;
                    next_c11_re = 256'sd0;
                end else if (in_beta != 32'sd1) begin
                    next_c00_re = $signed(in_beta) * $signed(in_c00_re);
                    next_c10_re = $signed(in_beta) * $signed(in_c10_re);
                    next_c10_im = $signed(in_beta) * $signed(in_c10_im);
                    next_c11_re = $signed(in_beta) * $signed(in_c11_re);
                end
            end
        end

        if (next_trans == 1'b0) begin
            if ((a0_re != 32'sd0) || (a0_im != 32'sd0) || (b0_re != 32'sd0) || (b0_im != 32'sd0)) begin
                temp1_0_re = next_alpha_re * b0_re + next_alpha_im * b0_im;
                temp1_0_im = next_alpha_im * b0_re - next_alpha_re * b0_im;
                temp2_0_re = next_alpha_conj_re * a0_re + next_alpha_conj_im * a0_im;
                temp2_0_im = next_alpha_conj_im * a0_re - next_alpha_conj_re * a0_im;
                diag0_re = a0_re * temp1_0_re - a0_im * temp1_0_im + b0_re * temp2_0_re - b0_im * temp2_0_im;
                next_c00_re = next_c00_re + diag0_re;
                if (!next_uplo) begin
                    add01_re_t0 = a1_re * temp1_0_re - a1_im * temp1_0_im + b1_re * temp2_0_re - b1_im * temp2_0_im;
                    add01_im_t0 = a1_re * temp1_0_im + a1_im * temp1_0_re + b1_re * temp2_0_im + b1_im * temp2_0_re;
                    next_c10_re = next_c10_re + add01_re_t0;
                    next_c10_im = next_c10_im + add01_im_t0;
                end
            end

            if ((a1_re != 32'sd0) || (a1_im != 32'sd0) || (b1_re != 32'sd0) || (b1_im != 32'sd0)) begin
                temp1_1_re = next_alpha_re * b1_re + next_alpha_im * b1_im;
                temp1_1_im = next_alpha_im * b1_re - next_alpha_re * b1_im;
                temp2_1_re = next_alpha_conj_re * a1_re + next_alpha_conj_im * a1_im;
                temp2_1_im = next_alpha_conj_im * a1_re - next_alpha_conj_re * a1_im;
                diag1_re = a1_re * temp1_1_re - a1_im * temp1_1_im + b1_re * temp2_1_re - b1_im * temp2_1_im;
                next_c11_re = next_c11_re + diag1_re;
                if (next_uplo) begin
                    add10_re_t0 = a0_re * temp1_1_re - a0_im * temp1_1_im + b0_re * temp2_1_re - b0_im * temp2_1_im;
                    add10_im_t0 = a0_re * temp1_1_im + a0_im * temp1_1_re + b0_re * temp2_1_im + b0_im * temp2_1_re;
                    next_c01_re = next_c01_re + add10_re_t0;
                    next_c01_im = next_c01_im + add10_im_t0;
                end
            end
        end else begin
            t00_0_re = a0_re * b0_re + a0_im * b0_im;
            t00_0_im = -a0_im * b0_re + a0_re * b0_im;
            u00_0_re = b0_re * a0_re + b0_im * a0_im;
            u00_0_im = -b0_im * a0_re + b0_re * a0_im;
            add00_re_t1 = next_alpha_re * t00_0_re - next_alpha_im * t00_0_im
                        + next_alpha_conj_re * u00_0_re - next_alpha_conj_im * u00_0_im;
            next_c00_re = next_c00_re + add00_re_t1;

            t11_0_re = a1_re * b1_re + a1_im * b1_im;
            t11_0_im = -a1_im * b1_re + a1_re * b1_im;
            u11_0_re = b1_re * a1_re + b1_im * a1_im;
            u11_0_im = -b1_im * a1_re + b1_re * a1_im;
            add11_re_t1 = next_alpha_re * t11_0_re - next_alpha_im * t11_0_im
                        + next_alpha_conj_re * u11_0_re - next_alpha_conj_im * u11_0_im;
            next_c11_re = next_c11_re + add11_re_t1;

            if (next_uplo) begin
                t01_re = a0_re * b1_re + a0_im * b1_im;
                t01_im = -a0_im * b1_re + a0_re * b1_im;
                u01_re = b0_re * a1_re + b0_im * a1_im;
                u01_im = -b0_im * a1_re + b0_re * a1_im;
                add01_re_t1 = next_alpha_re * t01_re - next_alpha_im * t01_im
                            + next_alpha_conj_re * u01_re - next_alpha_conj_im * u01_im;
                add01_im_t1 = next_alpha_re * t01_im + next_alpha_im * t01_re
                            + next_alpha_conj_re * u01_im + next_alpha_conj_im * u01_re;
                next_c01_re = next_c01_re + add01_re_t1;
                next_c01_im = next_c01_im + add01_im_t1;
            end else begin
                t10_re = a1_re * b0_re + a1_im * b0_im;
                t10_im = -a1_im * b0_re + a1_re * b0_im;
                u10_re = b1_re * a0_re + b1_im * a0_im;
                u10_im = -b1_im * a0_re + b1_re * a0_im;
                add10_re_t1 = next_alpha_re * t10_re - next_alpha_im * t10_im
                            + next_alpha_conj_re * u10_re - next_alpha_conj_im * u10_im;
                add10_im_t1 = next_alpha_re * t10_im + next_alpha_im * t10_re
                            + next_alpha_conj_re * u10_im + next_alpha_conj_im * u10_re;
                next_c10_re = next_c10_re + add10_re_t1;
                next_c10_im = next_c10_im + add10_im_t1;
            end
        end

        uplo_reg <= next_uplo;
        trans_reg <= next_trans;
        alpha_re_reg <= next_alpha_re;
        alpha_im_reg <= next_alpha_im;
        alpha_conj_re_reg <= next_alpha_conj_re;
        alpha_conj_im_reg <= next_alpha_conj_im;
        beta_reg <= next_beta;

        c00_re_reg <= next_c00_re;
        c01_re_reg <= next_c01_re;
        c01_im_reg <= next_c01_im;
        c10_re_reg <= next_c10_re;
        c10_im_reg <= next_c10_im;
        c11_re_reg <= next_c11_re;

        neg_next_c01_im = -next_c01_im;
        neg_next_c10_im = -next_c10_im;

        if (in_last) begin
            out_uplo_reg <= next_uplo;
            out_c00_re_reg <= next_c00_re[31:0];
            out_c00_im_reg <= 32'sd0;
            if (next_uplo) begin
                out_c01_re_reg <= next_c01_re[31:0];
                out_c01_im_reg <= next_c01_im[31:0];
                out_c10_re_reg <= next_c01_re[31:0];
                out_c10_im_reg <= neg_next_c01_im[31:0];
            end else begin
                out_c10_re_reg <= next_c10_re[31:0];
                out_c10_im_reg <= next_c10_im[31:0];
                out_c01_re_reg <= next_c10_re[31:0];
                out_c01_im_reg <= neg_next_c10_im[31:0];
            end
            out_c11_re_reg <= next_c11_re[31:0];
            out_c11_im_reg <= 32'sd0;
            busy <= 1'b1;
        end else begin
            busy <= 1'b0;
        end
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
