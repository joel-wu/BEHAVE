module HermitianMatrixVectorStep4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_uplo,

    input  logic [31:0]  in_alpha_re,
    input  logic [31:0]  in_alpha_im,

    input  logic [31:0]  in_beta_re,
    input  logic [31:0]  in_beta_im,

    input  logic [31:0]  in_x0_re,
    input  logic [31:0]  in_x0_im,
    input  logic [31:0]  in_x1_re,
    input  logic [31:0]  in_x1_im,
    input  logic [31:0]  in_x2_re,
    input  logic [31:0]  in_x2_im,
    input  logic [31:0]  in_x3_re,
    input  logic [31:0]  in_x3_im,

    input  logic [31:0]  in_y0_re,
    input  logic [31:0]  in_y0_im,
    input  logic [31:0]  in_y1_re,
    input  logic [31:0]  in_y1_im,
    input  logic [31:0]  in_y2_re,
    input  logic [31:0]  in_y2_im,
    input  logic [31:0]  in_y3_re,
    input  logic [31:0]  in_y3_im,

    input  logic [31:0]  in_a00_re,
    input  logic [31:0]  in_a00_im,
    input  logic [31:0]  in_a01_re,
    input  logic [31:0]  in_a01_im,
    input  logic [31:0]  in_a02_re,
    input  logic [31:0]  in_a02_im,
    input  logic [31:0]  in_a03_re,
    input  logic [31:0]  in_a03_im,
    input  logic [31:0]  in_a11_re,
    input  logic [31:0]  in_a11_im,
    input  logic [31:0]  in_a12_re,
    input  logic [31:0]  in_a12_im,
    input  logic [31:0]  in_a13_re,
    input  logic [31:0]  in_a13_im,
    input  logic [31:0]  in_a22_re,
    input  logic [31:0]  in_a22_im,
    input  logic [31:0]  in_a23_re,
    input  logic [31:0]  in_a23_im,
    input  logic [31:0]  in_a33_re,
    input  logic [31:0]  in_a33_im,

    output logic         out_valid,
    input  logic         out_ready,

    output logic [31:0]  out_y0_re,
    output logic [31:0]  out_y0_im,
    output logic [31:0]  out_y1_re,
    output logic [31:0]  out_y1_im,
    output logic [31:0]  out_y2_re,
    output logic [31:0]  out_y2_im,
    output logic [31:0]  out_y3_re,
    output logic [31:0]  out_y3_im
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic signed [31:0] conj_im(
        input logic [31:0] im_u
    );
        logic signed [31:0] im_s;
        begin
            im_s = $signed(im_u);
            conj_im = -im_s;
        end
    endfunction

    function automatic signed [63:0] cmul_re32(
        input logic signed [31:0] ar,
        input logic signed [31:0] ai,
        input logic signed [31:0] br,
        input logic signed [31:0] bi
    );
        logic signed [63:0] p0;
        logic signed [63:0] p1;
        begin
            p0 = ar * br;
            p1 = ai * bi;
            cmul_re32 = p0 - p1;
        end
    endfunction

    function automatic signed [63:0] cmul_im32(
        input logic signed [31:0] ar,
        input logic signed [31:0] ai,
        input logic signed [31:0] br,
        input logic signed [31:0] bi
    );
        logic signed [63:0] p0;
        logic signed [63:0] p1;
        begin
            p0 = ar * bi;
            p1 = ai * br;
            cmul_im32 = p0 + p1;
        end
    endfunction

    function automatic logic [31:0] low32(
        input logic signed [127:0] v
    );
        begin
            low32 = v[31:0];
        end
    endfunction

    always_ff @(posedge clk) begin
        if (rst) begin
            busy      <= 1'b0;
            out_y0_re <= 32'd0;
            out_y0_im <= 32'd0;
            out_y1_re <= 32'd0;
            out_y1_im <= 32'd0;
            out_y2_re <= 32'd0;
            out_y2_im <= 32'd0;
            out_y3_re <= 32'd0;
            out_y3_im <= 32'd0;
        end else if (input_accepted) begin
            logic signed [31:0] alpha_re_s;
            logic signed [31:0] alpha_im_s;
            logic signed [31:0] beta_re_s;
            logic signed [31:0] beta_im_s;

            logic signed [31:0] x0_re_s, x0_im_s, x1_re_s, x1_im_s, x2_re_s, x2_im_s, x3_re_s, x3_im_s;
            logic signed [31:0] y0_re_s, y0_im_s, y1_re_s, y1_im_s, y2_re_s, y2_im_s, y3_re_s, y3_im_s;

            logic signed [31:0] a00_re_s, a11_re_s, a22_re_s, a33_re_s;
            logic signed [31:0] a01_re_s, a01_im_s, a02_re_s, a02_im_s, a03_re_s, a03_im_s;
            logic signed [31:0] a12_re_s, a12_im_s, a13_re_s, a13_im_s, a23_re_s, a23_im_s;

            logic signed [31:0] A00_re, A00_im, A01_re, A01_im, A02_re, A02_im, A03_re, A03_im;
            logic signed [31:0] A10_re, A10_im, A11_re, A11_im, A12_re, A12_im, A13_re, A13_im;
            logic signed [31:0] A20_re, A20_im, A21_re, A21_im, A22_re, A22_im, A23_re, A23_im;
            logic signed [31:0] A30_re, A30_im, A31_re, A31_im, A32_re, A32_im, A33_re, A33_im;

            logic signed [63:0] t00_re, t00_im, t01_re, t01_im, t02_re, t02_im, t03_re, t03_im;
            logic signed [63:0] t10_re, t10_im, t11_re, t11_im, t12_re, t12_im, t13_re, t13_im;
            logic signed [63:0] t20_re, t20_im, t21_re, t21_im, t22_re, t22_im, t23_re, t23_im;
            logic signed [63:0] t30_re, t30_im, t31_re, t31_im, t32_re, t32_im, t33_re, t33_im;

            logic signed [65:0] mv0_re, mv0_im, mv1_re, mv1_im, mv2_re, mv2_im, mv3_re, mv3_im;

            logic signed [97:0] p0_re, p0_im, p1_re, p1_im, p2_re, p2_im, p3_re, p3_im;
            logic signed [97:0] q0_re, q0_im, q1_re, q1_im, q2_re, q2_im, q3_re, q3_im;

            logic signed [98:0] y0o_re, y0o_im, y1o_re, y1o_im, y2o_re, y2o_im, y3o_re, y3o_im;

            alpha_re_s = $signed(in_alpha_re);
            alpha_im_s = $signed(in_alpha_im);
            beta_re_s  = $signed(in_beta_re);
            beta_im_s  = $signed(in_beta_im);

            x0_re_s = $signed(in_x0_re); x0_im_s = $signed(in_x0_im);
            x1_re_s = $signed(in_x1_re); x1_im_s = $signed(in_x1_im);
            x2_re_s = $signed(in_x2_re); x2_im_s = $signed(in_x2_im);
            x3_re_s = $signed(in_x3_re); x3_im_s = $signed(in_x3_im);

            y0_re_s = $signed(in_y0_re); y0_im_s = $signed(in_y0_im);
            y1_re_s = $signed(in_y1_re); y1_im_s = $signed(in_y1_im);
            y2_re_s = $signed(in_y2_re); y2_im_s = $signed(in_y2_im);
            y3_re_s = $signed(in_y3_re); y3_im_s = $signed(in_y3_im);

            a00_re_s = $signed(in_a00_re);
            a11_re_s = $signed(in_a11_re);
            a22_re_s = $signed(in_a22_re);
            a33_re_s = $signed(in_a33_re);

            a01_re_s = $signed(in_a01_re); a01_im_s = $signed(in_a01_im);
            a02_re_s = $signed(in_a02_re); a02_im_s = $signed(in_a02_im);
            a03_re_s = $signed(in_a03_re); a03_im_s = $signed(in_a03_im);
            a12_re_s = $signed(in_a12_re); a12_im_s = $signed(in_a12_im);
            a13_re_s = $signed(in_a13_re); a13_im_s = $signed(in_a13_im);
            a23_re_s = $signed(in_a23_re); a23_im_s = $signed(in_a23_im);

            A00_re = a00_re_s; A00_im = 32'sd0;
            A11_re = a11_re_s; A11_im = 32'sd0;
            A22_re = a22_re_s; A22_im = 32'sd0;
            A33_re = a33_re_s; A33_im = 32'sd0;

            if (in_uplo) begin
                A01_re = a01_re_s;         A01_im = a01_im_s;
                A02_re = a02_re_s;         A02_im = a02_im_s;
                A03_re = a03_re_s;         A03_im = a03_im_s;
                A12_re = a12_re_s;         A12_im = a12_im_s;
                A13_re = a13_re_s;         A13_im = a13_im_s;
                A23_re = a23_re_s;         A23_im = a23_im_s;

                A10_re = a01_re_s;         A10_im = conj_im(in_a01_im);
                A20_re = a02_re_s;         A20_im = conj_im(in_a02_im);
                A30_re = a03_re_s;         A30_im = conj_im(in_a03_im);
                A21_re = a12_re_s;         A21_im = conj_im(in_a12_im);
                A31_re = a13_re_s;         A31_im = conj_im(in_a13_im);
                A32_re = a23_re_s;         A32_im = conj_im(in_a23_im);
            end else begin
                A10_re = a01_re_s;         A10_im = a01_im_s;
                A20_re = a02_re_s;         A20_im = a02_im_s;
                A30_re = a03_re_s;         A30_im = a03_im_s;
                A21_re = a12_re_s;         A21_im = a12_im_s;
                A31_re = a13_re_s;         A31_im = a13_im_s;
                A32_re = a23_re_s;         A32_im = a23_im_s;

                A01_re = a01_re_s;         A01_im = conj_im(in_a01_im);
                A02_re = a02_re_s;         A02_im = conj_im(in_a02_im);
                A03_re = a03_re_s;         A03_im = conj_im(in_a03_im);
                A12_re = a12_re_s;         A12_im = conj_im(in_a12_im);
                A13_re = a13_re_s;         A13_im = conj_im(in_a13_im);
                A23_re = a23_re_s;         A23_im = conj_im(in_a23_im);
            end

            t00_re = cmul_re32(A00_re, A00_im, x0_re_s, x0_im_s);
            t00_im = cmul_im32(A00_re, A00_im, x0_re_s, x0_im_s);
            t01_re = cmul_re32(A01_re, A01_im, x1_re_s, x1_im_s);
            t01_im = cmul_im32(A01_re, A01_im, x1_re_s, x1_im_s);
            t02_re = cmul_re32(A02_re, A02_im, x2_re_s, x2_im_s);
            t02_im = cmul_im32(A02_re, A02_im, x2_re_s, x2_im_s);
            t03_re = cmul_re32(A03_re, A03_im, x3_re_s, x3_im_s);
            t03_im = cmul_im32(A03_re, A03_im, x3_re_s, x3_im_s);

            t10_re = cmul_re32(A10_re, A10_im, x0_re_s, x0_im_s);
            t10_im = cmul_im32(A10_re, A10_im, x0_re_s, x0_im_s);
            t11_re = cmul_re32(A11_re, A11_im, x1_re_s, x1_im_s);
            t11_im = cmul_im32(A11_re, A11_im, x1_re_s, x1_im_s);
            t12_re = cmul_re32(A12_re, A12_im, x2_re_s, x2_im_s);
            t12_im = cmul_im32(A12_re, A12_im, x2_re_s, x2_im_s);
            t13_re = cmul_re32(A13_re, A13_im, x3_re_s, x3_im_s);
            t13_im = cmul_im32(A13_re, A13_im, x3_re_s, x3_im_s);

            t20_re = cmul_re32(A20_re, A20_im, x0_re_s, x0_im_s);
            t20_im = cmul_im32(A20_re, A20_im, x0_re_s, x0_im_s);
            t21_re = cmul_re32(A21_re, A21_im, x1_re_s, x1_im_s);
            t21_im = cmul_im32(A21_re, A21_im, x1_re_s, x1_im_s);
            t22_re = cmul_re32(A22_re, A22_im, x2_re_s, x2_im_s);
            t22_im = cmul_im32(A22_re, A22_im, x2_re_s, x2_im_s);
            t23_re = cmul_re32(A23_re, A23_im, x3_re_s, x3_im_s);
            t23_im = cmul_im32(A23_re, A23_im, x3_re_s, x3_im_s);

            t30_re = cmul_re32(A30_re, A30_im, x0_re_s, x0_im_s);
            t30_im = cmul_im32(A30_re, A30_im, x0_re_s, x0_im_s);
            t31_re = cmul_re32(A31_re, A31_im, x1_re_s, x1_im_s);
            t31_im = cmul_im32(A31_re, A31_im, x1_re_s, x1_im_s);
            t32_re = cmul_re32(A32_re, A32_im, x2_re_s, x2_im_s);
            t32_im = cmul_im32(A32_re, A32_im, x2_re_s, x2_im_s);
            t33_re = cmul_re32(A33_re, A33_im, x3_re_s, x3_im_s);
            t33_im = cmul_im32(A33_re, A33_im, x3_re_s, x3_im_s);

            mv0_re = $signed(t00_re) + $signed(t01_re) + $signed(t02_re) + $signed(t03_re);
            mv0_im = $signed(t00_im) + $signed(t01_im) + $signed(t02_im) + $signed(t03_im);

            mv1_re = $signed(t10_re) + $signed(t11_re) + $signed(t12_re) + $signed(t13_re);
            mv1_im = $signed(t10_im) + $signed(t11_im) + $signed(t12_im) + $signed(t13_im);

            mv2_re = $signed(t20_re) + $signed(t21_re) + $signed(t22_re) + $signed(t23_re);
            mv2_im = $signed(t20_im) + $signed(t21_im) + $signed(t22_im) + $signed(t23_im);

            mv3_re = $signed(t30_re) + $signed(t31_re) + $signed(t32_re) + $signed(t33_re);
            mv3_im = $signed(t30_im) + $signed(t31_im) + $signed(t32_im) + $signed(t33_im);

            p0_re = ($signed(alpha_re_s) * $signed(mv0_re)) - ($signed(alpha_im_s) * $signed(mv0_im));
            p0_im = ($signed(alpha_re_s) * $signed(mv0_im)) + ($signed(alpha_im_s) * $signed(mv0_re));
            p1_re = ($signed(alpha_re_s) * $signed(mv1_re)) - ($signed(alpha_im_s) * $signed(mv1_im));
            p1_im = ($signed(alpha_re_s) * $signed(mv1_im)) + ($signed(alpha_im_s) * $signed(mv1_re));
            p2_re = ($signed(alpha_re_s) * $signed(mv2_re)) - ($signed(alpha_im_s) * $signed(mv2_im));
            p2_im = ($signed(alpha_re_s) * $signed(mv2_im)) + ($signed(alpha_im_s) * $signed(mv2_re));
            p3_re = ($signed(alpha_re_s) * $signed(mv3_re)) - ($signed(alpha_im_s) * $signed(mv3_im));
            p3_im = ($signed(alpha_re_s) * $signed(mv3_im)) + ($signed(alpha_im_s) * $signed(mv3_re));

            q0_re = ($signed(beta_re_s) * $signed(y0_re_s)) - ($signed(beta_im_s) * $signed(y0_im_s));
            q0_im = ($signed(beta_re_s) * $signed(y0_im_s)) + ($signed(beta_im_s) * $signed(y0_re_s));
            q1_re = ($signed(beta_re_s) * $signed(y1_re_s)) - ($signed(beta_im_s) * $signed(y1_im_s));
            q1_im = ($signed(beta_re_s) * $signed(y1_im_s)) + ($signed(beta_im_s) * $signed(y1_re_s));
            q2_re = ($signed(beta_re_s) * $signed(y2_re_s)) - ($signed(beta_im_s) * $signed(y2_im_s));
            q2_im = ($signed(beta_re_s) * $signed(y2_im_s)) + ($signed(beta_im_s) * $signed(y2_re_s));
            q3_re = ($signed(beta_re_s) * $signed(y3_re_s)) - ($signed(beta_im_s) * $signed(y3_im_s));
            q3_im = ($signed(beta_re_s) * $signed(y3_im_s)) + ($signed(beta_im_s) * $signed(y3_re_s));

            y0o_re = $signed(p0_re) + $signed(q0_re);
            y0o_im = $signed(p0_im) + $signed(q0_im);
            y1o_re = $signed(p1_re) + $signed(q1_re);
            y1o_im = $signed(p1_im) + $signed(q1_im);
            y2o_re = $signed(p2_re) + $signed(q2_re);
            y2o_im = $signed(p2_im) + $signed(q2_im);
            y3o_re = $signed(p3_re) + $signed(q3_re);
            y3o_im = $signed(p3_im) + $signed(q3_im);

            out_y0_re <= low32(y0o_re);
            out_y0_im <= low32(y0o_im);
            out_y1_re <= low32(y1o_re);
            out_y1_im <= low32(y1o_im);
            out_y2_re <= low32(y2o_re);
            out_y2_im <= low32(y2o_im);
            out_y3_re <= low32(y3o_re);
            out_y3_im <= low32(y3o_im);

            busy <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule