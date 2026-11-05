module hermitian_rankk_tile (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_uplo,
    input  logic         in_trans,
    input  logic [15:0]  in_alpha,
    input  logic [15:0]  in_beta,

    input  logic [15:0]  in_a00_re,
    input  logic [15:0]  in_a00_im,
    input  logic [15:0]  in_a01_re,
    input  logic [15:0]  in_a01_im,
    input  logic [15:0]  in_a10_re,
    input  logic [15:0]  in_a10_im,
    input  logic [15:0]  in_a11_re,
    input  logic [15:0]  in_a11_im,

    input  logic [15:0]  in_c00_re,
    input  logic [15:0]  in_c00_im,
    input  logic [15:0]  in_c01_re,
    input  logic [15:0]  in_c01_im,
    input  logic [15:0]  in_c10_re,
    input  logic [15:0]  in_c10_im,
    input  logic [15:0]  in_c11_re,
    input  logic [15:0]  in_c11_im,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_c00_re,
    output logic [15:0]  out_c00_im,
    output logic [15:0]  out_c01_re,
    output logic [15:0]  out_c01_im,
    output logic [15:0]  out_c10_re,
    output logic [15:0]  out_c10_im,
    output logic [15:0]  out_c11_re,
    output logic [15:0]  out_c11_im
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;
assign in_ready        = ~busy | output_consumed;
assign out_valid       = busy;

function automatic logic signed [15:0] wrap16(input logic signed [31:0] v);
    begin
        wrap16 = v[15:0];
    end
endfunction

function automatic logic signed [15:0] qmul16(
    input logic signed [15:0] x,
    input logic signed [15:0] y
);
    logic signed [31:0] prod;
    logic signed [31:0] shifted;
    begin
        prod = x * y;
        shifted = prod >>> 12;
        qmul16 = wrap16(shifted);
    end
endfunction

function automatic logic signed [15:0] conj_im16(input logic signed [15:0] im);
    begin
        conj_im16 = wrap16(-im);
    end
endfunction

function automatic logic complex_is_zero16(
    input logic signed [15:0] re,
    input logic signed [15:0] im
);
    begin
        complex_is_zero16 = (re == 16'sd0) && (im == 16'sd0);
    end
endfunction

function automatic logic signed [15:0] real_conj_self16(
    input logic signed [15:0] re,
    input logic signed [15:0] im
);
    logic signed [15:0] rr;
    logic signed [15:0] ii;
    begin
        rr = qmul16(re, re);
        ii = qmul16(im, im);
        real_conj_self16 = wrap16(rr + ii);
    end
endfunction

function automatic logic signed [15:0] cmul_re16(
    input logic signed [15:0] ar,
    input logic signed [15:0] ai,
    input logic signed [15:0] br,
    input logic signed [15:0] bi
);
    logic signed [15:0] t0;
    logic signed [15:0] t1;
    begin
        t0 = qmul16(ar, br);
        t1 = qmul16(ai, bi);
        cmul_re16 = wrap16(t0 - t1);
    end
endfunction

function automatic logic signed [15:0] cmul_im16(
    input logic signed [15:0] ar,
    input logic signed [15:0] ai,
    input logic signed [15:0] br,
    input logic signed [15:0] bi
);
    logic signed [15:0] t0;
    logic signed [15:0] t1;
    begin
        t0 = qmul16(ar, bi);
        t1 = qmul16(ai, br);
        cmul_im16 = wrap16(t0 + t1);
    end
endfunction

logic signed [15:0] alpha_s;
logic signed [15:0] beta_s;
logic signed [15:0] a00_re_s, a00_im_s, a01_re_s, a01_im_s;
logic signed [15:0] a10_re_s, a10_im_s, a11_re_s, a11_im_s;
logic signed [15:0] c00_re_s, c01_re_s, c01_im_s, c10_re_s, c10_im_s, c11_re_s;

logic signed [15:0] next_c00_re, next_c00_im;
logic signed [15:0] next_c01_re, next_c01_im;
logic signed [15:0] next_c10_re, next_c10_im;
logic signed [15:0] next_c11_re, next_c11_im;

logic signed [15:0] acc00, acc11;
logic signed [15:0] acc01_re, acc01_im, acc10_re, acc10_im;
logic signed [15:0] temp_re, temp_im, prod_re, prod_im;
logic signed [15:0] rtemp00, rtemp11;
logic signed [15:0] m0_re, m0_im, m1_re, m1_im;
logic signed [15:0] temp01_re, temp01_im, temp10_re, temp10_im;
logic signed [15:0] s01_re, s01_im, s10_re, s10_im;

always_comb begin
    alpha_s = $signed(in_alpha);
    beta_s  = $signed(in_beta);

    a00_re_s = $signed(in_a00_re);
    a00_im_s = $signed(in_a00_im);
    a01_re_s = $signed(in_a01_re);
    a01_im_s = $signed(in_a01_im);
    a10_re_s = $signed(in_a10_re);
    a10_im_s = $signed(in_a10_im);
    a11_re_s = $signed(in_a11_re);
    a11_im_s = $signed(in_a11_im);

    c00_re_s = $signed(in_c00_re);
    c01_re_s = $signed(in_c01_re);
    c01_im_s = $signed(in_c01_im);
    c10_re_s = $signed(in_c10_re);
    c10_im_s = $signed(in_c10_im);
    c11_re_s = $signed(in_c11_re);

    next_c00_re = 16'sd0;
    next_c00_im = 16'sd0;
    next_c01_re = 16'sd0;
    next_c01_im = 16'sd0;
    next_c10_re = 16'sd0;
    next_c10_im = 16'sd0;
    next_c11_re = 16'sd0;
    next_c11_im = 16'sd0;

    acc00 = 16'sd0;
    acc11 = 16'sd0;
    acc01_re = 16'sd0;
    acc01_im = 16'sd0;
    acc10_re = 16'sd0;
    acc10_im = 16'sd0;
    temp_re = 16'sd0;
    temp_im = 16'sd0;
    prod_re = 16'sd0;
    prod_im = 16'sd0;
    rtemp00 = 16'sd0;
    rtemp11 = 16'sd0;
    m0_re = 16'sd0;
    m0_im = 16'sd0;
    m1_re = 16'sd0;
    m1_im = 16'sd0;
    temp01_re = 16'sd0;
    temp01_im = 16'sd0;
    temp10_re = 16'sd0;
    temp10_im = 16'sd0;
    s01_re = 16'sd0;
    s01_im = 16'sd0;
    s10_re = 16'sd0;
    s10_im = 16'sd0;

    if (in_trans == 1'b0) begin
        if (in_uplo == 1'b1) begin
            if (beta_s == 16'sd0) begin
                acc00 = 16'sd0;
            end else begin
                acc00 = qmul16(beta_s, c00_re_s);
            end
            if (!complex_is_zero16(a00_re_s, a00_im_s)) begin
                acc00 = wrap16(acc00 + qmul16(alpha_s, real_conj_self16(a00_re_s, a00_im_s)));
            end
            if (!complex_is_zero16(a01_re_s, a01_im_s)) begin
                acc00 = wrap16(acc00 + qmul16(alpha_s, real_conj_self16(a01_re_s, a01_im_s)));
            end
            next_c00_re = acc00;

            if (beta_s == 16'sd0) begin
                acc01_re = 16'sd0;
                acc01_im = 16'sd0;
            end else begin
                acc01_re = qmul16(beta_s, c01_re_s);
                acc01_im = qmul16(beta_s, c01_im_s);
            end
            if (!complex_is_zero16(a10_re_s, a10_im_s)) begin
                temp_re = qmul16(alpha_s, a10_re_s);
                temp_im = qmul16(alpha_s, conj_im16(a10_im_s));
                prod_re = cmul_re16(temp_re, temp_im, a00_re_s, a00_im_s);
                prod_im = cmul_im16(temp_re, temp_im, a00_re_s, a00_im_s);
                acc01_re = wrap16(acc01_re + prod_re);
                acc01_im = wrap16(acc01_im + prod_im);
            end
            if (!complex_is_zero16(a11_re_s, a11_im_s)) begin
                temp_re = qmul16(alpha_s, a11_re_s);
                temp_im = qmul16(alpha_s, conj_im16(a11_im_s));
                prod_re = cmul_re16(temp_re, temp_im, a01_re_s, a01_im_s);
                prod_im = cmul_im16(temp_re, temp_im, a01_re_s, a01_im_s);
                acc01_re = wrap16(acc01_re + prod_re);
                acc01_im = wrap16(acc01_im + prod_im);
            end
            next_c01_re = acc01_re;
            next_c01_im = acc01_im;

            if (beta_s == 16'sd0) begin
                acc11 = 16'sd0;
            end else begin
                acc11 = qmul16(beta_s, c11_re_s);
            end
            if (!complex_is_zero16(a10_re_s, a10_im_s)) begin
                acc11 = wrap16(acc11 + qmul16(alpha_s, real_conj_self16(a10_re_s, a10_im_s)));
            end
            if (!complex_is_zero16(a11_re_s, a11_im_s)) begin
                acc11 = wrap16(acc11 + qmul16(alpha_s, real_conj_self16(a11_re_s, a11_im_s)));
            end
            next_c11_re = acc11;
        end else begin
            if (beta_s == 16'sd0) begin
                acc00 = 16'sd0;
            end else if (beta_s == 16'sh1000) begin
                acc00 = c00_re_s;
            end else begin
                acc00 = qmul16(beta_s, c00_re_s);
            end
            if (!complex_is_zero16(a00_re_s, a00_im_s)) begin
                acc00 = wrap16(acc00 + qmul16(alpha_s, real_conj_self16(a00_re_s, a00_im_s)));
            end
            if (!complex_is_zero16(a01_re_s, a01_im_s)) begin
                acc00 = wrap16(acc00 + qmul16(alpha_s, real_conj_self16(a01_re_s, a01_im_s)));
            end
            next_c00_re = acc00;

            if (beta_s == 16'sd0) begin
                acc10_re = 16'sd0;
                acc10_im = 16'sd0;
            end else if (beta_s == 16'sh1000) begin
                acc10_re = c10_re_s;
                acc10_im = c10_im_s;
            end else begin
                acc10_re = qmul16(beta_s, c10_re_s);
                acc10_im = qmul16(beta_s, c10_im_s);
            end
            if (!complex_is_zero16(a00_re_s, a00_im_s)) begin
                temp_re = qmul16(alpha_s, a00_re_s);
                temp_im = qmul16(alpha_s, conj_im16(a00_im_s));
                prod_re = cmul_re16(temp_re, temp_im, a10_re_s, a10_im_s);
                prod_im = cmul_im16(temp_re, temp_im, a10_re_s, a10_im_s);
                acc10_re = wrap16(acc10_re + prod_re);
                acc10_im = wrap16(acc10_im + prod_im);
            end
            if (!complex_is_zero16(a01_re_s, a01_im_s)) begin
                temp_re = qmul16(alpha_s, a01_re_s);
                temp_im = qmul16(alpha_s, conj_im16(a01_im_s));
                prod_re = cmul_re16(temp_re, temp_im, a11_re_s, a11_im_s);
                prod_im = cmul_im16(temp_re, temp_im, a11_re_s, a11_im_s);
                acc10_re = wrap16(acc10_re + prod_re);
                acc10_im = wrap16(acc10_im + prod_im);
            end
            next_c10_re = acc10_re;
            next_c10_im = acc10_im;

            if (beta_s == 16'sd0) begin
                acc11 = 16'sd0;
            end else if (beta_s == 16'sh1000) begin
                acc11 = c11_re_s;
            end else begin
                acc11 = qmul16(beta_s, c11_re_s);
            end
            if (!complex_is_zero16(a10_re_s, a10_im_s)) begin
                acc11 = wrap16(acc11 + qmul16(alpha_s, real_conj_self16(a10_re_s, a10_im_s)));
            end
            if (!complex_is_zero16(a11_re_s, a11_im_s)) begin
                acc11 = wrap16(acc11 + qmul16(alpha_s, real_conj_self16(a11_re_s, a11_im_s)));
            end
            next_c11_re = acc11;
        end
    end else begin
        rtemp00 = wrap16(real_conj_self16(a00_re_s, a00_im_s) + real_conj_self16(a10_re_s, a10_im_s));
        rtemp11 = wrap16(real_conj_self16(a01_re_s, a01_im_s) + real_conj_self16(a11_re_s, a11_im_s));

        if (in_uplo == 1'b1) begin
            if (beta_s == 16'sd0) begin
                next_c00_re = qmul16(alpha_s, rtemp00);
            end else begin
                next_c00_re = wrap16(qmul16(alpha_s, rtemp00) + qmul16(beta_s, c00_re_s));
            end

            m0_re = cmul_re16(a00_re_s, conj_im16(a00_im_s), a01_re_s, a01_im_s);
            m0_im = cmul_im16(a00_re_s, conj_im16(a00_im_s), a01_re_s, a01_im_s);
            m1_re = cmul_re16(a10_re_s, conj_im16(a10_im_s), a11_re_s, a11_im_s);
            m1_im = cmul_im16(a10_re_s, conj_im16(a10_im_s), a11_re_s, a11_im_s);
            temp01_re = wrap16(m0_re + m1_re);
            temp01_im = wrap16(m0_im + m1_im);
            s01_re = qmul16(alpha_s, temp01_re);
            s01_im = qmul16(alpha_s, temp01_im);
            if (beta_s == 16'sd0) begin
                next_c01_re = s01_re;
                next_c01_im = s01_im;
            end else begin
                next_c01_re = wrap16(s01_re + qmul16(beta_s, c01_re_s));
                next_c01_im = wrap16(s01_im + qmul16(beta_s, c01_im_s));
            end

            if (beta_s == 16'sd0) begin
                next_c11_re = qmul16(alpha_s, rtemp11);
            end else begin
                next_c11_re = wrap16(qmul16(alpha_s, rtemp11) + qmul16(beta_s, c11_re_s));
            end
        end else begin
            if (beta_s == 16'sd0) begin
                next_c00_re = qmul16(alpha_s, rtemp00);
            end else begin
                next_c00_re = wrap16(qmul16(alpha_s, rtemp00) + qmul16(beta_s, c00_re_s));
            end

            m0_re = cmul_re16(a01_re_s, conj_im16(a01_im_s), a00_re_s, a00_im_s);
            m0_im = cmul_im16(a01_re_s, conj_im16(a01_im_s), a00_re_s, a00_im_s);
            m1_re = cmul_re16(a11_re_s, conj_im16(a11_im_s), a10_re_s, a10_im_s);
            m1_im = cmul_im16(a11_re_s, conj_im16(a11_im_s), a10_re_s, a10_im_s);
            temp10_re = wrap16(m0_re + m1_re);
            temp10_im = wrap16(m0_im + m1_im);
            s10_re = qmul16(alpha_s, temp10_re);
            s10_im = qmul16(alpha_s, temp10_im);
            if (beta_s == 16'sd0) begin
                next_c10_re = s10_re;
                next_c10_im = s10_im;
            end else begin
                next_c10_re = wrap16(s10_re + qmul16(beta_s, c10_re_s));
                next_c10_im = wrap16(s10_im + qmul16(beta_s, c10_im_s));
            end

            if (beta_s == 16'sd0) begin
                next_c11_re = qmul16(alpha_s, rtemp11);
            end else begin
                next_c11_re = wrap16(qmul16(alpha_s, rtemp11) + qmul16(beta_s, c11_re_s));
            end
        end
    end

    next_c00_im = 16'sd0;
    next_c11_im = 16'sd0;
    if (in_uplo == 1'b1) begin
        next_c10_re = 16'sd0;
        next_c10_im = 16'sd0;
    end else begin
        next_c01_re = 16'sd0;
        next_c01_im = 16'sd0;
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy       <= 1'b0;
        out_c00_re <= 16'h0000;
        out_c00_im <= 16'h0000;
        out_c01_re <= 16'h0000;
        out_c01_im <= 16'h0000;
        out_c10_re <= 16'h0000;
        out_c10_im <= 16'h0000;
        out_c11_re <= 16'h0000;
        out_c11_im <= 16'h0000;
    end else if (input_accepted) begin
        out_c00_re <= next_c00_re[15:0];
        out_c00_im <= next_c00_im[15:0];
        out_c01_re <= next_c01_re[15:0];
        out_c01_im <= next_c01_im[15:0];
        out_c10_re <= next_c10_re[15:0];
        out_c10_im <= next_c10_im[15:0];
        out_c11_re <= next_c11_re[15:0];
        out_c11_im <= next_c11_im[15:0];
        busy       <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule