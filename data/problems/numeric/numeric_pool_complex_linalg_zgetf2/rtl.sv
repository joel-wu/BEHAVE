module ComplexLuPivotStep4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_a00_re,
    input  logic [31:0]  in_a00_im,
    input  logic [31:0]  in_a01_re,
    input  logic [31:0]  in_a01_im,
    input  logic [31:0]  in_a02_re,
    input  logic [31:0]  in_a02_im,
    input  logic [31:0]  in_a03_re,
    input  logic [31:0]  in_a03_im,
    input  logic [31:0]  in_a10_re,
    input  logic [31:0]  in_a10_im,
    input  logic [31:0]  in_a11_re,
    input  logic [31:0]  in_a11_im,
    input  logic [31:0]  in_a12_re,
    input  logic [31:0]  in_a12_im,
    input  logic [31:0]  in_a13_re,
    input  logic [31:0]  in_a13_im,
    input  logic [31:0]  in_a20_re,
    input  logic [31:0]  in_a20_im,
    input  logic [31:0]  in_a21_re,
    input  logic [31:0]  in_a21_im,
    input  logic [31:0]  in_a22_re,
    input  logic [31:0]  in_a22_im,
    input  logic [31:0]  in_a23_re,
    input  logic [31:0]  in_a23_im,
    input  logic [31:0]  in_a30_re,
    input  logic [31:0]  in_a30_im,
    input  logic [31:0]  in_a31_re,
    input  logic [31:0]  in_a31_im,
    input  logic [31:0]  in_a32_re,
    input  logic [31:0]  in_a32_im,
    input  logic [31:0]  in_a33_re,
    input  logic [31:0]  in_a33_im,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_a00_re,
    output logic [31:0]  out_a00_im,
    output logic [31:0]  out_a01_re,
    output logic [31:0]  out_a01_im,
    output logic [31:0]  out_a02_re,
    output logic [31:0]  out_a02_im,
    output logic [31:0]  out_a03_re,
    output logic [31:0]  out_a03_im,
    output logic [31:0]  out_a10_re,
    output logic [31:0]  out_a10_im,
    output logic [31:0]  out_a11_re,
    output logic [31:0]  out_a11_im,
    output logic [31:0]  out_a12_re,
    output logic [31:0]  out_a12_im,
    output logic [31:0]  out_a13_re,
    output logic [31:0]  out_a13_im,
    output logic [31:0]  out_a20_re,
    output logic [31:0]  out_a20_im,
    output logic [31:0]  out_a21_re,
    output logic [31:0]  out_a21_im,
    output logic [31:0]  out_a22_re,
    output logic [31:0]  out_a22_im,
    output logic [31:0]  out_a23_re,
    output logic [31:0]  out_a23_im,
    output logic [31:0]  out_a30_re,
    output logic [31:0]  out_a30_im,
    output logic [31:0]  out_a31_re,
    output logic [31:0]  out_a31_im,
    output logic [31:0]  out_a32_re,
    output logic [31:0]  out_a32_im,
    output logic [31:0]  out_a33_re,
    output logic [31:0]  out_a33_im,
    output logic [1:0]   out_ipiv0,
    output logic [1:0]   out_ipiv1,
    output logic [1:0]   out_ipiv2,
    output logic [1:0]   out_ipiv3,
    output logic [2:0]   out_info
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign in_ready        = ~busy | output_consumed;
    assign out_valid       = busy;

    function automatic logic signed [31:0] wrap32(input logic signed [63:0] v);
        begin
            wrap32 = v[31:0];
        end
    endfunction

    function automatic logic signed [31:0] abs32_math(input logic signed [31:0] v);
        begin
            if (v >= 0)
                abs32_math = v;
            else
                abs32_math = -v;
        end
    endfunction

    function automatic logic [32:0] c_abs1(
        input logic signed [31:0] xr,
        input logic signed [31:0] xi
    );
        logic signed [31:0] ar;
        logic signed [31:0] ai;
        begin
            ar = abs32_math(xr);
            ai = abs32_math(xi);
            c_abs1 = $unsigned(ar) + $unsigned(ai);
        end
    endfunction

    function automatic logic signed [95:0] trunc_zero_div96(
        input logic signed [95:0] n,
        input logic signed [95:0] d
    );
        logic signed [95:0] an;
        logic signed [95:0] ad;
        logic signed [95:0] q;
        begin
            trunc_zero_div96 = '0;
            if (d == 0) begin
                trunc_zero_div96 = '0;
            end else begin
                an = (n < 0) ? -n : n;
                ad = (d < 0) ? -d : d;
                q = an / ad;
                if ((n < 0) ^ (d < 0))
                    trunc_zero_div96 = -q;
                else
                    trunc_zero_div96 = q;
            end
        end
    endfunction

    function automatic logic signed [31:0] c_mul_re_q16(
        input logic signed [31:0] xr,
        input logic signed [31:0] xi,
        input logic signed [31:0] yr,
        input logic signed [31:0] yi
    );
        logic signed [63:0] p_rr;
        logic signed [63:0] p_ii;
        logic signed [63:0] diff64;
        logic signed [95:0] num;
        logic signed [95:0] q;
        begin
            p_rr = xr * yr;
            p_ii = xi * yi;
            diff64 = p_rr - p_ii;
            num = $signed({{32{diff64[63]}}, diff64});
            q = trunc_zero_div96(num, 96'sd65536);
            c_mul_re_q16 = wrap32(q[63:0]);
        end
    endfunction

    function automatic logic signed [31:0] c_mul_im_q16(
        input logic signed [31:0] xr,
        input logic signed [31:0] xi,
        input logic signed [31:0] yr,
        input logic signed [31:0] yi
    );
        logic signed [63:0] p_ri;
        logic signed [63:0] p_ir;
        logic signed [63:0] sum64;
        logic signed [95:0] num;
        logic signed [95:0] q;
        begin
            p_ri = xr * yi;
            p_ir = xi * yr;
            sum64 = p_ri + p_ir;
            num = $signed({{32{sum64[63]}}, sum64});
            q = trunc_zero_div96(num, 96'sd65536);
            c_mul_im_q16 = wrap32(q[63:0]);
        end
    endfunction

    // Extend exact products before addition: a squared norm can equal 2^63.
    function automatic logic signed [95:0] product_wide(
        input logic signed [31:0] a,
        input logic signed [31:0] b
    );
        logic signed [63:0] product;
        begin
            product = a * b;
            product_wide = $signed({{32{product[63]}}, product});
        end
    endfunction

    function automatic logic signed [31:0] c_div_re_q16(
        input logic signed [31:0] xr,
        input logic signed [31:0] xi,
        input logic signed [31:0] yr,
        input logic signed [31:0] yi
    );
        logic signed [95:0] den;
        logic signed [95:0] num_re;
        logic signed [95:0] num_scaled;
        logic signed [95:0] q;
        begin
            den = product_wide(yr, yr) + product_wide(yi, yi);
            if (den == 0) begin
                c_div_re_q16 = 32'sd0;
            end else begin
                num_re = product_wide(xr, yr) + product_wide(xi, yi);
                num_scaled = num_re <<< 16;
                q = trunc_zero_div96(num_scaled, den);
                c_div_re_q16 = wrap32(q[63:0]);
            end
        end
    endfunction

    function automatic logic signed [31:0] c_div_im_q16(
        input logic signed [31:0] xr,
        input logic signed [31:0] xi,
        input logic signed [31:0] yr,
        input logic signed [31:0] yi
    );
        logic signed [95:0] den;
        logic signed [95:0] num_im;
        logic signed [95:0] num_scaled;
        logic signed [95:0] q;
        begin
            den = product_wide(yr, yr) + product_wide(yi, yi);
            if (den == 0) begin
                c_div_im_q16 = 32'sd0;
            end else begin
                num_im = product_wide(xi, yr) - product_wide(xr, yi);
                num_scaled = num_im <<< 16;
                q = trunc_zero_div96(num_scaled, den);
                c_div_im_q16 = wrap32(q[63:0]);
            end
        end
    endfunction

    function automatic logic signed [31:0] c_sub_wrap32(
        input logic signed [31:0] a,
        input logic signed [31:0] b
    );
        logic signed [63:0] diff;
        begin
            diff = a - b;
            c_sub_wrap32 = wrap32(diff);
        end
    endfunction

    logic signed [31:0] a_re [0:3][0:3];
    logic signed [31:0] a_im [0:3][0:3];
    logic [1:0] ipiv [0:3];
    logic [2:0] info_reg;

    integer j;
    integer r;
    integer c;
    integer jp;
    logic [32:0] best_mag;
    logic [32:0] cur_mag;
    logic signed [31:0] tmp_re;
    logic signed [31:0] tmp_im;
    logic signed [31:0] mul_re;
    logic signed [31:0] mul_im;
    logic signed [31:0] old_rj_re;
    logic signed [31:0] old_rj_im;
    logic pivot_nonzero;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;

            out_a00_re <= 32'd0; out_a00_im <= 32'd0;
            out_a01_re <= 32'd0; out_a01_im <= 32'd0;
            out_a02_re <= 32'd0; out_a02_im <= 32'd0;
            out_a03_re <= 32'd0; out_a03_im <= 32'd0;
            out_a10_re <= 32'd0; out_a10_im <= 32'd0;
            out_a11_re <= 32'd0; out_a11_im <= 32'd0;
            out_a12_re <= 32'd0; out_a12_im <= 32'd0;
            out_a13_re <= 32'd0; out_a13_im <= 32'd0;
            out_a20_re <= 32'd0; out_a20_im <= 32'd0;
            out_a21_re <= 32'd0; out_a21_im <= 32'd0;
            out_a22_re <= 32'd0; out_a22_im <= 32'd0;
            out_a23_re <= 32'd0; out_a23_im <= 32'd0;
            out_a30_re <= 32'd0; out_a30_im <= 32'd0;
            out_a31_re <= 32'd0; out_a31_im <= 32'd0;
            out_a32_re <= 32'd0; out_a32_im <= 32'd0;
            out_a33_re <= 32'd0; out_a33_im <= 32'd0;
            out_ipiv0  <= 2'd0;
            out_ipiv1  <= 2'd0;
            out_ipiv2  <= 2'd0;
            out_ipiv3  <= 2'd0;
            out_info   <= 3'd0;
        end else if (input_accepted) begin
            a_re[0][0] = $signed(in_a00_re); a_im[0][0] = $signed(in_a00_im);
            a_re[0][1] = $signed(in_a01_re); a_im[0][1] = $signed(in_a01_im);
            a_re[0][2] = $signed(in_a02_re); a_im[0][2] = $signed(in_a02_im);
            a_re[0][3] = $signed(in_a03_re); a_im[0][3] = $signed(in_a03_im);

            a_re[1][0] = $signed(in_a10_re); a_im[1][0] = $signed(in_a10_im);
            a_re[1][1] = $signed(in_a11_re); a_im[1][1] = $signed(in_a11_im);
            a_re[1][2] = $signed(in_a12_re); a_im[1][2] = $signed(in_a12_im);
            a_re[1][3] = $signed(in_a13_re); a_im[1][3] = $signed(in_a13_im);

            a_re[2][0] = $signed(in_a20_re); a_im[2][0] = $signed(in_a20_im);
            a_re[2][1] = $signed(in_a21_re); a_im[2][1] = $signed(in_a21_im);
            a_re[2][2] = $signed(in_a22_re); a_im[2][2] = $signed(in_a22_im);
            a_re[2][3] = $signed(in_a23_re); a_im[2][3] = $signed(in_a23_im);

            a_re[3][0] = $signed(in_a30_re); a_im[3][0] = $signed(in_a30_im);
            a_re[3][1] = $signed(in_a31_re); a_im[3][1] = $signed(in_a31_im);
            a_re[3][2] = $signed(in_a32_re); a_im[3][2] = $signed(in_a32_im);
            a_re[3][3] = $signed(in_a33_re); a_im[3][3] = $signed(in_a33_im);

            ipiv[0] = 2'd0;
            ipiv[1] = 2'd0;
            ipiv[2] = 2'd0;
            ipiv[3] = 2'd0;
            info_reg = 3'd0;

            for (j = 0; j < 4; j = j + 1) begin
                jp = j;
                best_mag = c_abs1(a_re[j][j], a_im[j][j]);
                for (r = j + 1; r < 4; r = r + 1) begin
                    cur_mag = c_abs1(a_re[r][j], a_im[r][j]);
                    if (cur_mag > best_mag) begin
                        best_mag = cur_mag;
                        jp = r;
                    end
                end
                ipiv[j] = jp[1:0];

                pivot_nonzero = ~((a_re[jp][j] == 32'sd0) && (a_im[jp][j] == 32'sd0));

                if (pivot_nonzero) begin
                    if (jp != j) begin
                        for (c = 0; c < 4; c = c + 1) begin
                            tmp_re = a_re[j][c];
                            tmp_im = a_im[j][c];
                            a_re[j][c] = a_re[jp][c];
                            a_im[j][c] = a_im[jp][c];
                            a_re[jp][c] = tmp_re;
                            a_im[jp][c] = tmp_im;
                        end
                    end
                    for (r = j + 1; r < 4; r = r + 1) begin
                        old_rj_re = a_re[r][j];
                        old_rj_im = a_im[r][j];
                        a_re[r][j] = c_div_re_q16(old_rj_re, old_rj_im, a_re[j][j], a_im[j][j]);
                        a_im[r][j] = c_div_im_q16(old_rj_re, old_rj_im, a_re[j][j], a_im[j][j]);
                    end
                end else begin
                    if (info_reg == 3'd0)
                        info_reg = j + 1;
                end

                if (j < 3) begin
                    for (r = j + 1; r < 4; r = r + 1) begin
                        for (c = j + 1; c < 4; c = c + 1) begin
                            mul_re = c_mul_re_q16(a_re[r][j], a_im[r][j], a_re[j][c], a_im[j][c]);
                            mul_im = c_mul_im_q16(a_re[r][j], a_im[r][j], a_re[j][c], a_im[j][c]);
                            a_re[r][c] = c_sub_wrap32(a_re[r][c], mul_re);
                            a_im[r][c] = c_sub_wrap32(a_im[r][c], mul_im);
                        end
                    end
                end
            end

            out_a00_re <= a_re[0][0]; out_a00_im <= a_im[0][0];
            out_a01_re <= a_re[0][1]; out_a01_im <= a_im[0][1];
            out_a02_re <= a_re[0][2]; out_a02_im <= a_im[0][2];
            out_a03_re <= a_re[0][3]; out_a03_im <= a_im[0][3];

            out_a10_re <= a_re[1][0]; out_a10_im <= a_im[1][0];
            out_a11_re <= a_re[1][1]; out_a11_im <= a_im[1][1];
            out_a12_re <= a_re[1][2]; out_a12_im <= a_im[1][2];
            out_a13_re <= a_re[1][3]; out_a13_im <= a_im[1][3];

            out_a20_re <= a_re[2][0]; out_a20_im <= a_im[2][0];
            out_a21_re <= a_re[2][1]; out_a21_im <= a_im[2][1];
            out_a22_re <= a_re[2][2]; out_a22_im <= a_im[2][2];
            out_a23_re <= a_re[2][3]; out_a23_im <= a_im[2][3];

            out_a30_re <= a_re[3][0]; out_a30_im <= a_im[3][0];
            out_a31_re <= a_re[3][1]; out_a31_im <= a_im[3][1];
            out_a32_re <= a_re[3][2]; out_a32_im <= a_im[3][2];
            out_a33_re <= a_re[3][3]; out_a33_im <= a_im[3][3];

            out_ipiv0 <= ipiv[0];
            out_ipiv1 <= ipiv[1];
            out_ipiv2 <= ipiv[2];
            out_ipiv3 <= ipiv[3];
            out_info  <= info_reg;

            busy <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
