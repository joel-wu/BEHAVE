module radix4_fft_butterfly_f32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_inverse,

    input  logic [31:0] in_xa_re,
    input  logic [31:0] in_xa_im,
    input  logic [31:0] in_xb_re,
    input  logic [31:0] in_xb_im,
    input  logic [31:0] in_xc_re,
    input  logic [31:0] in_xc_im,
    input  logic [31:0] in_xd_re,
    input  logic [31:0] in_xd_im,

    input  logic [31:0] in_w1_re,
    input  logic [31:0] in_w1_im,
    input  logic [31:0] in_w2_re,
    input  logic [31:0] in_w2_im,
    input  logic [31:0] in_w3_re,
    input  logic [31:0] in_w3_im,

    output logic        out_valid,
    input  logic        out_ready,

    output logic [31:0] out_y0_re,
    output logic [31:0] out_y0_im,
    output logic [31:0] out_y1_re,
    output logic [31:0] out_y1_im,
    output logic [31:0] out_y2_re,
    output logic [31:0] out_y2_im,
    output logic [31:0] out_y3_re,
    output logic [31:0] out_y3_im
);

function automatic logic [63:0] fp_shift_jam(input logic [63:0] x, input integer n);
    if (n <= 0) fp_shift_jam = x;
    else if (n >= 64) fp_shift_jam = {63'b0, |x};
    else fp_shift_jam = (x >> n) | {63'b0, |(x << (64-n))};
endfunction

function automatic logic [31:0] fp_round_pack(
    input logic sign_bit, input integer exponent, input logic [63:0] sig
);
    integer e;
    logic [63:0] shifted, rounded;
    begin
        e = exponent;
        shifted = sig;
        if (e < 1) begin
            shifted = fp_shift_jam(shifted, 1-e);
            e = 1;
        end
        rounded = (shifted >> 3) + {63'b0, (shifted[2] &&
                  (shifted[1] || shifted[0] || shifted[3]))};
        if (rounded[24]) begin
            rounded = rounded >> 1;
            e = e+1;
        end
        if (e >= 255) fp_round_pack = {sign_bit, 8'hff, 23'b0};
        else if (rounded < 64'h800000)
            fp_round_pack = {sign_bit, 8'b0, rounded[22:0]};
        else fp_round_pack = {sign_bit, e[7:0], rounded[22:0]};
    end
endfunction

function automatic logic [31:0] fp32_add_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sa, sb, sign_bit;
    logic [63:0] ma, mb, hi, lo, sum;
    begin
        sa = a[31]; sb = b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000))
            fp32_add_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) && (b[30:0] == 31'h7f800000) && (sa != sb))
            fp32_add_bits = 32'h7fc00000;
        else if (a[30:0] == 31'h7f800000) fp32_add_bits = a;
        else if (b[30:0] == 31'h7f800000) fp32_add_bits = b;
        else if ((a[30:0] == 0) && (b[30:0] == 0))
            fp32_add_bits = {sa & sb, 31'b0};
        else begin
            if ((ea > eb) || ((ea == eb) && (ma >= mb))) begin
                hi = ma << 3; lo = fp_shift_jam(mb << 3, ea-eb);
                sign_bit = sa; e = ea;
            end else begin
                hi = mb << 3; lo = fp_shift_jam(ma << 3, eb-ea);
                sign_bit = sb; e = eb;
            end
            sum = sa == sb ? hi+lo : hi-lo;
            if (sum == 0) fp32_add_bits = 32'b0;
            else begin
                if (sum[27]) begin
                    sum = fp_shift_jam(sum, 1); e = e+1;
                end
                for (i=0; i<26; i=i+1) begin
                    if (!sum[26] && e>1) begin sum=sum<<1; e=e-1; end
                end
                fp32_add_bits = fp_round_pack(sign_bit, e, sum);
            end
        end
    end
endfunction

function automatic logic [31:0] fp32_sub_bits(input logic [31:0] a, b);
    fp32_sub_bits = fp32_add_bits(a, b ^ 32'h80000000);
endfunction

function automatic logic [31:0] fp32_mul_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sign_bit;
    logic [63:0] ma, mb, product, sig;
    begin
        sign_bit = a[31] ^ b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000) ||
            ((a[30:0] == 31'h7f800000) && (b[30:0] == 0)) ||
            ((b[30:0] == 31'h7f800000) && (a[30:0] == 0)))
            fp32_mul_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) || (b[30:0] == 31'h7f800000))
            fp32_mul_bits = {sign_bit, 8'hff, 23'b0};
        else if ((a[30:0] == 0) || (b[30:0] == 0))
            fp32_mul_bits = {sign_bit, 31'b0};
        else begin
            for (i=0; i<23; i=i+1) begin
                if (!ma[23]) begin ma=ma<<1; ea=ea-1; end
                if (!mb[23]) begin mb=mb<<1; eb=eb-1; end
            end
            product = ma*mb;
            e = ea+eb-127;
            if (product[47]) begin sig=fp_shift_jam(product,21); e=e+1; end
            else sig=fp_shift_jam(product,20);
            fp32_mul_bits = fp_round_pack(sign_bit, e, sig);
        end
    end
endfunction

    localparam logic [31:0] ONE_BY_FOUR = 32'h3E800000;

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    logic [31:0] Xaplusc;
    logic [31:0] Xaminusc;
    logic [31:0] Yaplusc;
    logic [31:0] Yaminusc;
    logic [31:0] Xbplusd;
    logic [31:0] Xbminusd;
    logic [31:0] Ybplusd;
    logic [31:0] Ybminusd;
    logic [31:0] y0_re_pre;
    logic [31:0] y0_im_pre;
    logic [31:0] b1_re_pre;
    logic [31:0] b1_im_pre;
    logic [31:0] b2_re_pre;
    logic [31:0] b2_im_pre;
    logic [31:0] b3_re_pre;
    logic [31:0] b3_im_pre;
    logic [31:0] t10;
    logic [31:0] t11;
    logic [31:0] t12;
    logic [31:0] t13;
    logic [31:0] t20;
    logic [31:0] t21;
    logic [31:0] t22;
    logic [31:0] t23;
    logic [31:0] t30;
    logic [31:0] t31;
    logic [31:0] t32;
    logic [31:0] t33;
    logic [31:0] calc_y0_re;
    logic [31:0] calc_y0_im;
    logic [31:0] calc_y1_re;
    logic [31:0] calc_y1_im;
    logic [31:0] calc_y2_re;
    logic [31:0] calc_y2_im;
    logic [31:0] calc_y3_re;
    logic [31:0] calc_y3_im;
    logic [31:0] y1_re_pre;
    logic [31:0] y1_im_pre;
    logic [31:0] y2_re_pre;
    logic [31:0] y2_im_pre;
    logic [31:0] y3_re_pre;
    logic [31:0] y3_im_pre;

    always_comb begin
        Xaplusc  = fp32_add_bits(in_xa_re, in_xc_re);
        Xaminusc = fp32_sub_bits(in_xa_re, in_xc_re);
        Yaplusc  = fp32_add_bits(in_xa_im, in_xc_im);
        Yaminusc = fp32_sub_bits(in_xa_im, in_xc_im);
        Xbplusd  = fp32_add_bits(in_xb_re, in_xd_re);
        Xbminusd = fp32_sub_bits(in_xb_re, in_xd_re);
        Ybplusd  = fp32_add_bits(in_xb_im, in_xd_im);
        Ybminusd = fp32_sub_bits(in_xb_im, in_xd_im);

        y0_re_pre = fp32_add_bits(Xaplusc, Xbplusd);
        y0_im_pre = fp32_add_bits(Yaplusc, Ybplusd);

        b1_re_pre = 32'd0;
        b1_im_pre = 32'd0;
        b2_re_pre = 32'd0;
        b2_im_pre = 32'd0;
        b3_re_pre = 32'd0;
        b3_im_pre = 32'd0;

        t10 = 32'd0;
        t11 = 32'd0;
        t12 = 32'd0;
        t13 = 32'd0;
        t20 = 32'd0;
        t21 = 32'd0;
        t22 = 32'd0;
        t23 = 32'd0;
        t30 = 32'd0;
        t31 = 32'd0;
        t32 = 32'd0;
        t33 = 32'd0;

        y1_re_pre = 32'd0;
        y1_im_pre = 32'd0;
        y2_re_pre = 32'd0;
        y2_im_pre = 32'd0;
        y3_re_pre = 32'd0;
        y3_im_pre = 32'd0;

        calc_y0_re = 32'd0;
        calc_y0_im = 32'd0;
        calc_y1_re = 32'd0;
        calc_y1_im = 32'd0;
        calc_y2_re = 32'd0;
        calc_y2_im = 32'd0;
        calc_y3_re = 32'd0;
        calc_y3_im = 32'd0;

        if (in_inverse) begin
            b1_re_pre = fp32_sub_bits(Xaminusc, Ybminusd);
            b1_im_pre = fp32_add_bits(Yaminusc, Xbminusd);
            b2_re_pre = fp32_sub_bits(Xaplusc, Xbplusd);
            b2_im_pre = fp32_sub_bits(Yaplusc, Ybplusd);
            b3_re_pre = fp32_add_bits(Xaminusc, Ybminusd);
            b3_im_pre = fp32_sub_bits(Yaminusc, Xbminusd);

            t20 = fp32_mul_bits(b2_re_pre, in_w2_re);
            t21 = fp32_mul_bits(b2_im_pre, in_w2_re);
            t22 = fp32_mul_bits(b2_im_pre, in_w2_im);
            t23 = fp32_mul_bits(b2_re_pre, in_w2_im);
            y1_re_pre = fp32_sub_bits(t20, t22);
            y1_im_pre = fp32_add_bits(t21, t23);

            t10 = fp32_mul_bits(b1_re_pre, in_w1_re);
            t11 = fp32_mul_bits(b1_im_pre, in_w1_re);
            t12 = fp32_mul_bits(b1_im_pre, in_w1_im);
            t13 = fp32_mul_bits(b1_re_pre, in_w1_im);
            y2_re_pre = fp32_sub_bits(t10, t12);
            y2_im_pre = fp32_add_bits(t11, t13);

            t30 = fp32_mul_bits(b3_re_pre, in_w3_re);
            t31 = fp32_mul_bits(b3_im_pre, in_w3_re);
            t32 = fp32_mul_bits(b3_im_pre, in_w3_im);
            t33 = fp32_mul_bits(b3_re_pre, in_w3_im);
            y3_re_pre = fp32_sub_bits(t30, t32);
            y3_im_pre = fp32_add_bits(t31, t33);

            calc_y0_re = fp32_mul_bits(y0_re_pre, ONE_BY_FOUR);
            calc_y0_im = fp32_mul_bits(y0_im_pre, ONE_BY_FOUR);
            calc_y1_re = fp32_mul_bits(y1_re_pre, ONE_BY_FOUR);
            calc_y1_im = fp32_mul_bits(y1_im_pre, ONE_BY_FOUR);
            calc_y2_re = fp32_mul_bits(y2_re_pre, ONE_BY_FOUR);
            calc_y2_im = fp32_mul_bits(y2_im_pre, ONE_BY_FOUR);
            calc_y3_re = fp32_mul_bits(y3_re_pre, ONE_BY_FOUR);
            calc_y3_im = fp32_mul_bits(y3_im_pre, ONE_BY_FOUR);
        end else begin
            b1_re_pre = fp32_add_bits(Xaminusc, Ybminusd);
            b1_im_pre = fp32_sub_bits(Yaminusc, Xbminusd);
            b2_re_pre = fp32_sub_bits(Xaplusc, Xbplusd);
            b2_im_pre = fp32_sub_bits(Yaplusc, Ybplusd);
            b3_re_pre = fp32_sub_bits(Xaminusc, Ybminusd);
            b3_im_pre = fp32_add_bits(Xbminusd, Yaminusc);

            t20 = fp32_mul_bits(b2_re_pre, in_w2_re);
            t21 = fp32_mul_bits(b2_im_pre, in_w2_re);
            t22 = fp32_mul_bits(b2_im_pre, in_w2_im);
            t23 = fp32_mul_bits(b2_re_pre, in_w2_im);
            calc_y1_re = fp32_add_bits(t20, t22);
            calc_y1_im = fp32_sub_bits(t21, t23);

            t10 = fp32_mul_bits(b1_re_pre, in_w1_re);
            t11 = fp32_mul_bits(b1_im_pre, in_w1_re);
            t12 = fp32_mul_bits(b1_im_pre, in_w1_im);
            t13 = fp32_mul_bits(b1_re_pre, in_w1_im);
            calc_y2_re = fp32_add_bits(t10, t12);
            calc_y2_im = fp32_sub_bits(t11, t13);

            t30 = fp32_mul_bits(b3_re_pre, in_w3_re);
            t31 = fp32_mul_bits(b3_im_pre, in_w3_re);
            t32 = fp32_mul_bits(b3_im_pre, in_w3_im);
            t33 = fp32_mul_bits(b3_re_pre, in_w3_im);
            calc_y3_re = fp32_add_bits(t30, t32);
            calc_y3_im = fp32_sub_bits(t31, t33);

            calc_y0_re = y0_re_pre;
            calc_y0_im = y0_im_pre;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy      <= 1'b0;
            out_y0_re <= 32'h00000000;
            out_y0_im <= 32'h00000000;
            out_y1_re <= 32'h00000000;
            out_y1_im <= 32'h00000000;
            out_y2_re <= 32'h00000000;
            out_y2_im <= 32'h00000000;
            out_y3_re <= 32'h00000000;
            out_y3_im <= 32'h00000000;
        end else if (input_accepted) begin
            busy      <= 1'b1;
            out_y0_re <= calc_y0_re;
            out_y0_im <= calc_y0_im;
            out_y1_re <= calc_y1_re;
            out_y1_im <= calc_y1_im;
            out_y2_re <= calc_y2_re;
            out_y2_im <= calc_y2_im;
            out_y3_re <= calc_y3_re;
            out_y3_im <= calc_y3_im;
        end else if (output_consumed) begin
            busy      <= 1'b0;
            out_y0_re <= 32'h00000000;
            out_y0_im <= 32'h00000000;
            out_y1_re <= 32'h00000000;
            out_y1_im <= 32'h00000000;
            out_y2_re <= 32'h00000000;
            out_y2_im <= 32'h00000000;
            out_y3_re <= 32'h00000000;
            out_y3_im <= 32'h00000000;
        end
    end

endmodule
