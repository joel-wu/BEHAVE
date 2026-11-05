module complex_gemv4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [1:0]   in_trans,
    input  logic [63:0]  in_alpha_re,
    input  logic [63:0]  in_alpha_im,
    input  logic [63:0]  in_beta_re,
    input  logic [63:0]  in_beta_im,

    input  logic [63:0]  in_a00_re,
    input  logic [63:0]  in_a00_im,
    input  logic [63:0]  in_a10_re,
    input  logic [63:0]  in_a10_im,
    input  logic [63:0]  in_a20_re,
    input  logic [63:0]  in_a20_im,
    input  logic [63:0]  in_a30_re,
    input  logic [63:0]  in_a30_im,
    input  logic [63:0]  in_a01_re,
    input  logic [63:0]  in_a01_im,
    input  logic [63:0]  in_a11_re,
    input  logic [63:0]  in_a11_im,
    input  logic [63:0]  in_a21_re,
    input  logic [63:0]  in_a21_im,
    input  logic [63:0]  in_a31_re,
    input  logic [63:0]  in_a31_im,
    input  logic [63:0]  in_a02_re,
    input  logic [63:0]  in_a02_im,
    input  logic [63:0]  in_a12_re,
    input  logic [63:0]  in_a12_im,
    input  logic [63:0]  in_a22_re,
    input  logic [63:0]  in_a22_im,
    input  logic [63:0]  in_a32_re,
    input  logic [63:0]  in_a32_im,
    input  logic [63:0]  in_a03_re,
    input  logic [63:0]  in_a03_im,
    input  logic [63:0]  in_a13_re,
    input  logic [63:0]  in_a13_im,
    input  logic [63:0]  in_a23_re,
    input  logic [63:0]  in_a23_im,
    input  logic [63:0]  in_a33_re,
    input  logic [63:0]  in_a33_im,

    input  logic [63:0]  in_x0_re,
    input  logic [63:0]  in_x0_im,
    input  logic [63:0]  in_x1_re,
    input  logic [63:0]  in_x1_im,
    input  logic [63:0]  in_x2_re,
    input  logic [63:0]  in_x2_im,
    input  logic [63:0]  in_x3_re,
    input  logic [63:0]  in_x3_im,

    input  logic [63:0]  in_y0_re,
    input  logic [63:0]  in_y0_im,
    input  logic [63:0]  in_y1_re,
    input  logic [63:0]  in_y1_im,
    input  logic [63:0]  in_y2_re,
    input  logic [63:0]  in_y2_im,
    input  logic [63:0]  in_y3_re,
    input  logic [63:0]  in_y3_im,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_y0_re,
    output logic [63:0]  out_y0_im,
    output logic [63:0]  out_y1_re,
    output logic [63:0]  out_y1_im,
    output logic [63:0]  out_y2_re,
    output logic [63:0]  out_y2_im,
    output logic [63:0]  out_y3_re,
    output logic [63:0]  out_y3_im
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    logic signed [63:0] alpha_re, alpha_im, beta_re, beta_im;
    logic signed [63:0] a00_re_s, a00_im_s, a10_re_s, a10_im_s, a20_re_s, a20_im_s, a30_re_s, a30_im_s;
    logic signed [63:0] a01_re_s, a01_im_s, a11_re_s, a11_im_s, a21_re_s, a21_im_s, a31_re_s, a31_im_s;
    logic signed [63:0] a02_re_s, a02_im_s, a12_re_s, a12_im_s, a22_re_s, a22_im_s, a32_re_s, a32_im_s;
    logic signed [63:0] a03_re_s, a03_im_s, a13_re_s, a13_im_s, a23_re_s, a23_im_s, a33_re_s, a33_im_s;
    logic signed [63:0] x0_re_s, x0_im_s, x1_re_s, x1_im_s, x2_re_s, x2_im_s, x3_re_s, x3_im_s;
    logic signed [63:0] y0_re_s, y0_im_s, y1_re_s, y1_im_s, y2_re_s, y2_im_s, y3_re_s, y3_im_s;

    logic signed [255:0] yw0_re, yw0_im, yw1_re, yw1_im, yw2_re, yw2_im, yw3_re, yw3_im;
    logic signed [255:0] tmp_re, tmp_im, t_re, t_im;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic signed [63:0] s64(input logic [63:0] x);
        s64 = $signed(x);
    endfunction

    function automatic logic [63:0] low64(input logic signed [255:0] x);
        low64 = x[63:0];
    endfunction

    function automatic logic signed [255:0] cmul_re64(
        input logic signed [63:0] ar,
        input logic signed [63:0] ai,
        input logic signed [63:0] br,
        input logic signed [63:0] bi
    );
        cmul_re64 = ar * br - ai * bi;
    endfunction

    function automatic logic signed [255:0] cmul_im64(
        input logic signed [63:0] ar,
        input logic signed [63:0] ai,
        input logic signed [63:0] br,
        input logic signed [63:0] bi
    );
        cmul_im64 = ar * bi + ai * br;
    endfunction

    function automatic logic signed [255:0] cmul_re256x64(
        input logic signed [255:0] ar,
        input logic signed [255:0] ai,
        input logic signed [63:0]  br,
        input logic signed [63:0]  bi
    );
        cmul_re256x64 = ar * br - ai * bi;
    endfunction

    function automatic logic signed [255:0] cmul_im256x64(
        input logic signed [255:0] ar,
        input logic signed [255:0] ai,
        input logic signed [63:0]  br,
        input logic signed [63:0]  bi
    );
        cmul_im256x64 = ar * bi + ai * br;
    endfunction

    function automatic logic signed [255:0] cmul_re64x256(
        input logic signed [63:0]  ar,
        input logic signed [63:0]  ai,
        input logic signed [255:0] br,
        input logic signed [255:0] bi
    );
        cmul_re64x256 = ar * br - ai * bi;
    endfunction

    function automatic logic signed [255:0] cmul_im64x256(
        input logic signed [63:0]  ar,
        input logic signed [63:0]  ai,
        input logic signed [255:0] br,
        input logic signed [255:0] bi
    );
        cmul_im64x256 = ar * bi + ai * br;
    endfunction

    function automatic logic signed [63:0] conj_im(
        input logic signed [63:0] ai
    );
        conj_im = -ai;
    endfunction

    always_comb begin
        alpha_re = s64(in_alpha_re);
        alpha_im = s64(in_alpha_im);
        beta_re  = s64(in_beta_re);
        beta_im  = s64(in_beta_im);

        a00_re_s = s64(in_a00_re); a00_im_s = s64(in_a00_im);
        a10_re_s = s64(in_a10_re); a10_im_s = s64(in_a10_im);
        a20_re_s = s64(in_a20_re); a20_im_s = s64(in_a20_im);
        a30_re_s = s64(in_a30_re); a30_im_s = s64(in_a30_im);

        a01_re_s = s64(in_a01_re); a01_im_s = s64(in_a01_im);
        a11_re_s = s64(in_a11_re); a11_im_s = s64(in_a11_im);
        a21_re_s = s64(in_a21_re); a21_im_s = s64(in_a21_im);
        a31_re_s = s64(in_a31_re); a31_im_s = s64(in_a31_im);

        a02_re_s = s64(in_a02_re); a02_im_s = s64(in_a02_im);
        a12_re_s = s64(in_a12_re); a12_im_s = s64(in_a12_im);
        a22_re_s = s64(in_a22_re); a22_im_s = s64(in_a22_im);
        a32_re_s = s64(in_a32_re); a32_im_s = s64(in_a32_im);

        a03_re_s = s64(in_a03_re); a03_im_s = s64(in_a03_im);
        a13_re_s = s64(in_a13_re); a13_im_s = s64(in_a13_im);
        a23_re_s = s64(in_a23_re); a23_im_s = s64(in_a23_im);
        a33_re_s = s64(in_a33_re); a33_im_s = s64(in_a33_im);

        x0_re_s = s64(in_x0_re); x0_im_s = s64(in_x0_im);
        x1_re_s = s64(in_x1_re); x1_im_s = s64(in_x1_im);
        x2_re_s = s64(in_x2_re); x2_im_s = s64(in_x2_im);
        x3_re_s = s64(in_x3_re); x3_im_s = s64(in_x3_im);

        y0_re_s = s64(in_y0_re); y0_im_s = s64(in_y0_im);
        y1_re_s = s64(in_y1_re); y1_im_s = s64(in_y1_im);
        y2_re_s = s64(in_y2_re); y2_im_s = s64(in_y2_im);
        y3_re_s = s64(in_y3_re); y3_im_s = s64(in_y3_im);

        yw0_re = 256'sd0; yw0_im = 256'sd0;
        yw1_re = 256'sd0; yw1_im = 256'sd0;
        yw2_re = 256'sd0; yw2_im = 256'sd0;
        yw3_re = 256'sd0; yw3_im = 256'sd0;
        tmp_re = 256'sd0; tmp_im = 256'sd0;
        t_re   = 256'sd0; t_im   = 256'sd0;

        if ((beta_re == 64'sd1) && (beta_im == 64'sd0)) begin
            yw0_re = y0_re_s; yw0_im = y0_im_s;
            yw1_re = y1_re_s; yw1_im = y1_im_s;
            yw2_re = y2_re_s; yw2_im = y2_im_s;
            yw3_re = y3_re_s; yw3_im = y3_im_s;
        end else if ((beta_re == 64'sd0) && (beta_im == 64'sd0)) begin
            yw0_re = 256'sd0; yw0_im = 256'sd0;
            yw1_re = 256'sd0; yw1_im = 256'sd0;
            yw2_re = 256'sd0; yw2_im = 256'sd0;
            yw3_re = 256'sd0; yw3_im = 256'sd0;
        end else begin
            yw0_re = cmul_re64(beta_re, beta_im, y0_re_s, y0_im_s);
            yw0_im = cmul_im64(beta_re, beta_im, y0_re_s, y0_im_s);
            yw1_re = cmul_re64(beta_re, beta_im, y1_re_s, y1_im_s);
            yw1_im = cmul_im64(beta_re, beta_im, y1_re_s, y1_im_s);
            yw2_re = cmul_re64(beta_re, beta_im, y2_re_s, y2_im_s);
            yw2_im = cmul_im64(beta_re, beta_im, y2_re_s, y2_im_s);
            yw3_re = cmul_re64(beta_re, beta_im, y3_re_s, y3_im_s);
            yw3_im = cmul_im64(beta_re, beta_im, y3_re_s, y3_im_s);
        end

        if (!((alpha_re == 64'sd0) && (alpha_im == 64'sd0))) begin
            if (in_trans == 2'b01) begin
                tmp_re = 256'sd0; tmp_im = 256'sd0;
                t_re = cmul_re64(a00_re_s, a00_im_s, x0_re_s, x0_im_s);
                t_im = cmul_im64(a00_re_s, a00_im_s, x0_re_s, x0_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a10_re_s, a10_im_s, x1_re_s, x1_im_s);
                t_im = cmul_im64(a10_re_s, a10_im_s, x1_re_s, x1_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a20_re_s, a20_im_s, x2_re_s, x2_im_s);
                t_im = cmul_im64(a20_re_s, a20_im_s, x2_re_s, x2_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a30_re_s, a30_im_s, x3_re_s, x3_im_s);
                t_im = cmul_im64(a30_re_s, a30_im_s, x3_re_s, x3_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64x256(alpha_re, alpha_im, tmp_re, tmp_im);
                t_im = cmul_im64x256(alpha_re, alpha_im, tmp_re, tmp_im);
                yw0_re = yw0_re + t_re; yw0_im = yw0_im + t_im;

                tmp_re = 256'sd0; tmp_im = 256'sd0;
                t_re = cmul_re64(a01_re_s, a01_im_s, x0_re_s, x0_im_s);
                t_im = cmul_im64(a01_re_s, a01_im_s, x0_re_s, x0_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a11_re_s, a11_im_s, x1_re_s, x1_im_s);
                t_im = cmul_im64(a11_re_s, a11_im_s, x1_re_s, x1_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a21_re_s, a21_im_s, x2_re_s, x2_im_s);
                t_im = cmul_im64(a21_re_s, a21_im_s, x2_re_s, x2_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a31_re_s, a31_im_s, x3_re_s, x3_im_s);
                t_im = cmul_im64(a31_re_s, a31_im_s, x3_re_s, x3_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64x256(alpha_re, alpha_im, tmp_re, tmp_im);
                t_im = cmul_im64x256(alpha_re, alpha_im, tmp_re, tmp_im);
                yw1_re = yw1_re + t_re; yw1_im = yw1_im + t_im;

                tmp_re = 256'sd0; tmp_im = 256'sd0;
                t_re = cmul_re64(a02_re_s, a02_im_s, x0_re_s, x0_im_s);
                t_im = cmul_im64(a02_re_s, a02_im_s, x0_re_s, x0_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a12_re_s, a12_im_s, x1_re_s, x1_im_s);
                t_im = cmul_im64(a12_re_s, a12_im_s, x1_re_s, x1_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a22_re_s, a22_im_s, x2_re_s, x2_im_s);
                t_im = cmul_im64(a22_re_s, a22_im_s, x2_re_s, x2_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a32_re_s, a32_im_s, x3_re_s, x3_im_s);
                t_im = cmul_im64(a32_re_s, a32_im_s, x3_re_s, x3_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64x256(alpha_re, alpha_im, tmp_re, tmp_im);
                t_im = cmul_im64x256(alpha_re, alpha_im, tmp_re, tmp_im);
                yw2_re = yw2_re + t_re; yw2_im = yw2_im + t_im;

                tmp_re = 256'sd0; tmp_im = 256'sd0;
                t_re = cmul_re64(a03_re_s, a03_im_s, x0_re_s, x0_im_s);
                t_im = cmul_im64(a03_re_s, a03_im_s, x0_re_s, x0_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a13_re_s, a13_im_s, x1_re_s, x1_im_s);
                t_im = cmul_im64(a13_re_s, a13_im_s, x1_re_s, x1_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a23_re_s, a23_im_s, x2_re_s, x2_im_s);
                t_im = cmul_im64(a23_re_s, a23_im_s, x2_re_s, x2_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a33_re_s, a33_im_s, x3_re_s, x3_im_s);
                t_im = cmul_im64(a33_re_s, a33_im_s, x3_re_s, x3_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64x256(alpha_re, alpha_im, tmp_re, tmp_im);
                t_im = cmul_im64x256(alpha_re, alpha_im, tmp_re, tmp_im);
                yw3_re = yw3_re + t_re; yw3_im = yw3_im + t_im;
            end else if (in_trans == 2'b10) begin
                tmp_re = 256'sd0; tmp_im = 256'sd0;
                t_re = cmul_re64(a00_re_s, conj_im(a00_im_s), x0_re_s, x0_im_s);
                t_im = cmul_im64(a00_re_s, conj_im(a00_im_s), x0_re_s, x0_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a10_re_s, conj_im(a10_im_s), x1_re_s, x1_im_s);
                t_im = cmul_im64(a10_re_s, conj_im(a10_im_s), x1_re_s, x1_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a20_re_s, conj_im(a20_im_s), x2_re_s, x2_im_s);
                t_im = cmul_im64(a20_re_s, conj_im(a20_im_s), x2_re_s, x2_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a30_re_s, conj_im(a30_im_s), x3_re_s, x3_im_s);
                t_im = cmul_im64(a30_re_s, conj_im(a30_im_s), x3_re_s, x3_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64x256(alpha_re, alpha_im, tmp_re, tmp_im);
                t_im = cmul_im64x256(alpha_re, alpha_im, tmp_re, tmp_im);
                yw0_re = yw0_re + t_re; yw0_im = yw0_im + t_im;

                tmp_re = 256'sd0; tmp_im = 256'sd0;
                t_re = cmul_re64(a01_re_s, conj_im(a01_im_s), x0_re_s, x0_im_s);
                t_im = cmul_im64(a01_re_s, conj_im(a01_im_s), x0_re_s, x0_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a11_re_s, conj_im(a11_im_s), x1_re_s, x1_im_s);
                t_im = cmul_im64(a11_re_s, conj_im(a11_im_s), x1_re_s, x1_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a21_re_s, conj_im(a21_im_s), x2_re_s, x2_im_s);
                t_im = cmul_im64(a21_re_s, conj_im(a21_im_s), x2_re_s, x2_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a31_re_s, conj_im(a31_im_s), x3_re_s, x3_im_s);
                t_im = cmul_im64(a31_re_s, conj_im(a31_im_s), x3_re_s, x3_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64x256(alpha_re, alpha_im, tmp_re, tmp_im);
                t_im = cmul_im64x256(alpha_re, alpha_im, tmp_re, tmp_im);
                yw1_re = yw1_re + t_re; yw1_im = yw1_im + t_im;

                tmp_re = 256'sd0; tmp_im = 256'sd0;
                t_re = cmul_re64(a02_re_s, conj_im(a02_im_s), x0_re_s, x0_im_s);
                t_im = cmul_im64(a02_re_s, conj_im(a02_im_s), x0_re_s, x0_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a12_re_s, conj_im(a12_im_s), x1_re_s, x1_im_s);
                t_im = cmul_im64(a12_re_s, conj_im(a12_im_s), x1_re_s, x1_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a22_re_s, conj_im(a22_im_s), x2_re_s, x2_im_s);
                t_im = cmul_im64(a22_re_s, conj_im(a22_im_s), x2_re_s, x2_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a32_re_s, conj_im(a32_im_s), x3_re_s, x3_im_s);
                t_im = cmul_im64(a32_re_s, conj_im(a32_im_s), x3_re_s, x3_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64x256(alpha_re, alpha_im, tmp_re, tmp_im);
                t_im = cmul_im64x256(alpha_re, alpha_im, tmp_re, tmp_im);
                yw2_re = yw2_re + t_re; yw2_im = yw2_im + t_im;

                tmp_re = 256'sd0; tmp_im = 256'sd0;
                t_re = cmul_re64(a03_re_s, conj_im(a03_im_s), x0_re_s, x0_im_s);
                t_im = cmul_im64(a03_re_s, conj_im(a03_im_s), x0_re_s, x0_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a13_re_s, conj_im(a13_im_s), x1_re_s, x1_im_s);
                t_im = cmul_im64(a13_re_s, conj_im(a13_im_s), x1_re_s, x1_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a23_re_s, conj_im(a23_im_s), x2_re_s, x2_im_s);
                t_im = cmul_im64(a23_re_s, conj_im(a23_im_s), x2_re_s, x2_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64(a33_re_s, conj_im(a33_im_s), x3_re_s, x3_im_s);
                t_im = cmul_im64(a33_re_s, conj_im(a33_im_s), x3_re_s, x3_im_s);
                tmp_re = tmp_re + t_re; tmp_im = tmp_im + t_im;
                t_re = cmul_re64x256(alpha_re, alpha_im, tmp_re, tmp_im);
                t_im = cmul_im64x256(alpha_re, alpha_im, tmp_re, tmp_im);
                yw3_re = yw3_re + t_re; yw3_im = yw3_im + t_im;
            end else begin
                tmp_re = cmul_re64(alpha_re, alpha_im, x0_re_s, x0_im_s);
                tmp_im = cmul_im64(alpha_re, alpha_im, x0_re_s, x0_im_s);
                t_re = cmul_re256x64(tmp_re, tmp_im, a00_re_s, a00_im_s);
                t_im = cmul_im256x64(tmp_re, tmp_im, a00_re_s, a00_im_s);
                yw0_re = yw0_re + t_re; yw0_im = yw0_im + t_im;
                t_re = cmul_re256x64(tmp_re, tmp_im, a10_re_s, a10_im_s);
                t_im = cmul_im256x64(tmp_re, tmp_im, a10_re_s, a10_im_s);
                yw1_re = yw1_re + t_re; yw1_im = yw1_im + t_im;
                t_re = cmul_re256x64(tmp_re, tmp_im, a20_re_s, a20_im_s);
                t_im = cmul_im256x64(tmp_re, tmp_im, a20_re_s, a20_im_s);
                yw2_re = yw2_re + t_re; yw2_im = yw2_im + t_im;
                t_re = cmul_re256x64(tmp_re, tmp_im, a30_re_s, a30_im_s);
                t_im = cmul_im256x64(tmp_re, tmp_im, a30_re_s, a30_im_s);
                yw3_re = yw3_re + t_re; yw3_im = yw3_im + t_im;

                tmp_re = cmul_re64(alpha_re, alpha_im, x1_re_s, x1_im_s);
                tmp_im = cmul_im64(alpha_re, alpha_im, x1_re_s, x1_im_s);
                t_re = cmul_re256x64(tmp_re, tmp_im, a01_re_s, a01_im_s);
                t_im = cmul_im256x64(tmp_re, tmp_im, a01_re_s, a01_im_s);
                yw0_re = yw0_re + t_re; yw0_im = yw0_im + t_im;
                t_re = cmul_re256x64(tmp_re, tmp_im, a11_re_s, a11_im_s);
                t_im = cmul_im256x64(tmp_re, tmp_im, a11_re_s, a11_im_s);
                yw1_re = yw1_re + t_re; yw1_im = yw1_im + t_im;
                t_re = cmul_re256x64(tmp_re, tmp_im, a21_re_s, a21_im_s);
                t_im = cmul_im256x64(tmp_re, tmp_im, a21_re_s, a21_im_s);
                yw2_re = yw2_re + t_re; yw2_im = yw2_im + t_im;
                t_re = cmul_re256x64(tmp_re, tmp_im, a31_re_s, a31_im_s);
                t_im = cmul_im256x64(tmp_re, tmp_im, a31_re_s, a31_im_s);
                yw3_re = yw3_re + t_re; yw3_im = yw3_im + t_im;

                tmp_re = cmul_re64(alpha_re, alpha_im, x2_re_s, x2_im_s);
                tmp_im = cmul_im64(alpha_re, alpha_im, x2_re_s, x2_im_s);
                t_re = cmul_re256x64(tmp_re, tmp_im, a02_re_s, a02_im_s);
                t_im = cmul_im256x64(tmp_re, tmp_im, a02_re_s, a02_im_s);
                yw0_re = yw0_re + t_re; yw0_im = yw0_im + t_im;
                t_re = cmul_re256x64(tmp_re, tmp_im, a12_re_s, a12_im_s);
                t_im = cmul_im256x64(tmp_re, tmp_im, a12_re_s, a12_im_s);
                yw1_re = yw1_re + t_re; yw1_im = yw1_im + t_im;
                t_re = cmul_re256x64(tmp_re, tmp_im, a22_re_s, a22_im_s);
                t_im = cmul_im256x64(tmp_re, tmp_im, a22_re_s, a22_im_s);
                yw2_re = yw2_re + t_re; yw2_im = yw2_im + t_im;
                t_re = cmul_re256x64(tmp_re, tmp_im, a32_re_s, a32_im_s);
                t_im = cmul_im256x64(tmp_re, tmp_im, a32_re_s, a32_im_s);
                yw3_re = yw3_re + t_re; yw3_im = yw3_im + t_im;

                tmp_re = cmul_re64(alpha_re, alpha_im, x3_re_s, x3_im_s);
                tmp_im = cmul_im64(alpha_re, alpha_im, x3_re_s, x3_im_s);
                t_re = cmul_re256x64(tmp_re, tmp_im, a03_re_s, a03_im_s);
                t_im = cmul_im256x64(tmp_re, tmp_im, a03_re_s, a03_im_s);
                yw0_re = yw0_re + t_re; yw0_im = yw0_im + t_im;
                t_re = cmul_re256x64(tmp_re, tmp_im, a13_re_s, a13_im_s);
                t_im = cmul_im256x64(tmp_re, tmp_im, a13_re_s, a13_im_s);
                yw1_re = yw1_re + t_re; yw1_im = yw1_im + t_im;
                t_re = cmul_re256x64(tmp_re, tmp_im, a23_re_s, a23_im_s);
                t_im = cmul_im256x64(tmp_re, tmp_im, a23_re_s, a23_im_s);
                yw2_re = yw2_re + t_re; yw2_im = yw2_im + t_im;
                t_re = cmul_re256x64(tmp_re, tmp_im, a33_re_s, a33_im_s);
                t_im = cmul_im256x64(tmp_re, tmp_im, a33_re_s, a33_im_s);
                yw3_re = yw3_re + t_re; yw3_im = yw3_im + t_im;
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy      <= 1'b0;
            out_y0_re <= 64'd0;
            out_y0_im <= 64'd0;
            out_y1_re <= 64'd0;
            out_y1_im <= 64'd0;
            out_y2_re <= 64'd0;
            out_y2_im <= 64'd0;
            out_y3_re <= 64'd0;
            out_y3_im <= 64'd0;
        end else if (input_accepted) begin
            out_y0_re <= low64(yw0_re);
            out_y0_im <= low64(yw0_im);
            out_y1_re <= low64(yw1_re);
            out_y1_im <= low64(yw1_im);
            out_y2_re <= low64(yw2_re);
            out_y2_im <= low64(yw2_im);
            out_y3_re <= low64(yw3_re);
            out_y3_im <= low64(yw3_im);
            busy      <= 1'b1;
        end else if (output_consumed) begin
            busy      <= 1'b0;
            out_y0_re <= 64'd0;
            out_y0_im <= 64'd0;
            out_y1_re <= 64'd0;
            out_y1_im <= 64'd0;
            out_y2_re <= 64'd0;
            out_y2_im <= 64'd0;
            out_y3_re <= 64'd0;
            out_y3_im <= 64'd0;
        end
    end

endmodule