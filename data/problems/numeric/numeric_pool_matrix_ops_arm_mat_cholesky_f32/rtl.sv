module cholesky4x4_fp32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a00,
    input  logic [31:0] in_a01,
    input  logic [31:0] in_a02,
    input  logic [31:0] in_a03,
    input  logic [31:0] in_a10,
    input  logic [31:0] in_a11,
    input  logic [31:0] in_a12,
    input  logic [31:0] in_a13,
    input  logic [31:0] in_a20,
    input  logic [31:0] in_a21,
    input  logic [31:0] in_a22,
    input  logic [31:0] in_a23,
    input  logic [31:0] in_a30,
    input  logic [31:0] in_a31,
    input  logic [31:0] in_a32,
    input  logic [31:0] in_a33,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_l00,
    output logic [31:0] out_l01,
    output logic [31:0] out_l02,
    output logic [31:0] out_l03,
    output logic [31:0] out_l10,
    output logic [31:0] out_l11,
    output logic [31:0] out_l12,
    output logic [31:0] out_l13,
    output logic [31:0] out_l20,
    output logic [31:0] out_l21,
    output logic [31:0] out_l22,
    output logic [31:0] out_l23,
    output logic [31:0] out_l30,
    output logic [31:0] out_l31,
    output logic [31:0] out_l32,
    output logic [31:0] out_l33,
    output logic [1:0]  out_status
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

function automatic logic [31:0] fp32_sqrt_bits(input logic [31:0] a);
    integer e, i;
    logic [63:0] mant, radicand, root, rem, trial;
    begin
        if (a[30:0] > 31'h7f800000) fp32_sqrt_bits = 32'h7fc00000;
        else if (a[30:0] == 0) fp32_sqrt_bits = a;
        else if (a[31]) fp32_sqrt_bits = 32'h7fc00000;
        else if (a[30:0] == 31'h7f800000) fp32_sqrt_bits = a;
        else begin
            e = a[30:23] == 0 ? 1 : int'(a[30:23]);
            mant = {40'b0, (a[30:23] != 0), a[22:0]};
            for (i=0; i<23; i=i+1) begin
                if (!mant[23]) begin mant=mant<<1; e=e-1; end
            end
            e = e-127;
            if (e & 1) mant = mant<<1;
            radicand = mant<<29;
            root = 0; rem = 0;
            for (i=26; i>=0; i=i-1) begin
                rem = (rem<<2) | ((radicand>>(2*i)) & 64'd3);
                trial = (root<<2) | 64'd1;
                root = root<<1;
                if (rem >= trial) begin rem=rem-trial; root=root|64'd1; end
            end
            root = root | {63'b0, (rem != 0)};
            fp32_sqrt_bits = fp_round_pack(1'b0, (e >>> 1)+127, root);
        end
    end
endfunction

function automatic logic [31:0] fp32_div_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sign_bit;
    logic [63:0] ma, mb, numerator, quotient, remainder;
    begin
        sign_bit = a[31] ^ b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000) ||
            ((a[30:0] == 31'h7f800000) && (b[30:0] == 31'h7f800000)) ||
            ((a[30:0] == 0) && (b[30:0] == 0)))
            fp32_div_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) || (b[30:0] == 0))
            fp32_div_bits = {sign_bit, 8'hff, 23'b0};
        else if ((a[30:0] == 0) || (b[30:0] == 31'h7f800000))
            fp32_div_bits = {sign_bit, 31'b0};
        else begin
            for (i=0; i<23; i=i+1) begin
                if (!ma[23]) begin ma=ma<<1; ea=ea-1; end
                if (!mb[23]) begin mb=mb<<1; eb=eb-1; end
            end
            e = ea-eb+127;
            if (ma < mb) begin ma=ma<<1; e=e-1; end
            numerator = ma<<26;
            quotient = numerator/mb;
            remainder = numerator%mb;
            quotient = quotient | {63'b0, (remainder != 0)};
            fp32_div_bits = fp_round_pack(sign_bit, e, quotient);
        end
    end
endfunction

    localparam logic [31:0] FP_ONE  = 32'h3F800000;
    localparam logic [31:0] FP_QNAN = 32'h7FC00000;
    localparam logic [31:0] FP_PINF = 32'h7F800000;

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign in_ready        = ~busy | output_consumed;
    assign out_valid       = busy;

    function automatic logic is_nan32(input logic [31:0] a);
        is_nan32 = (&a[30:23]) && (a[22:0] != 23'd0);
    endfunction

    function automatic logic is_zero32(input logic [31:0] a);
        is_zero32 = (a[30:0] == 31'd0);
    endfunction

    function automatic logic [31:0] flush_subnormal32(input logic [31:0] a);
    flush_subnormal32 = a;
endfunction

    function automatic logic diag_gt_zero(input logic [31:0] x_in);
        logic [31:0] x;
        begin
            x = flush_subnormal32(x_in);
            if (is_nan32(x)) diag_gt_zero = 1'b0;
            else if (is_zero32(x)) diag_gt_zero = 1'b0;
            else if (x[31]) diag_gt_zero = 1'b0;
            else diag_gt_zero = 1'b1;
        end
    endfunction

    // fp32_sqrt_bits — line-for-line translation of fp_lib.fp32_sqrt_bits.

    // digit-recurrence isqrt over a 53-bit target.

    logic [31:0] calc_l00, calc_l01, calc_l02, calc_l03;
    logic [31:0] calc_l10, calc_l11, calc_l12, calc_l13;
    logic [31:0] calc_l20, calc_l21, calc_l22, calc_l23;
    logic [31:0] calc_l30, calc_l31, calc_l32, calc_l33;
    logic [1:0]  calc_status;

    logic [31:0] g00, g10, g20, g30;
    logic [31:0] r0, l00_t, l10_t, l20_t, l30_t;
    logic [31:0] g11, g21, g31;
    logic [31:0] r1, l11_t, l21_t, l31_t;
    logic [31:0] t20, g22, t30, g32;
    logic [31:0] r2, l22_t, l32_t;
    logic [31:0] t31, t32, g33;
    logic [31:0] r3, l33_t;

    always_comb begin
        calc_l00 = 32'd0;
        calc_l01 = 32'd0;
        calc_l02 = 32'd0;
        calc_l03 = 32'd0;
        calc_l10 = 32'd0;
        calc_l11 = 32'd0;
        calc_l12 = 32'd0;
        calc_l13 = 32'd0;
        calc_l20 = 32'd0;
        calc_l21 = 32'd0;
        calc_l22 = 32'd0;
        calc_l23 = 32'd0;
        calc_l30 = 32'd0;
        calc_l31 = 32'd0;
        calc_l32 = 32'd0;
        calc_l33 = 32'd0;
        calc_status = 2'd1;

        g00 = in_a00;
        g10 = in_a10;
        g20 = in_a20;
        g30 = in_a30;

        r0 = 32'd0;
        l00_t = 32'd0;
        l10_t = 32'd0;
        l20_t = 32'd0;
        l30_t = 32'd0;
        g11 = 32'd0;
        g21 = 32'd0;
        g31 = 32'd0;
        r1 = 32'd0;
        l11_t = 32'd0;
        l21_t = 32'd0;
        l31_t = 32'd0;
        t20 = 32'd0;
        g22 = 32'd0;
        t30 = 32'd0;
        g32 = 32'd0;
        r2 = 32'd0;
        l22_t = 32'd0;
        l32_t = 32'd0;
        t31 = 32'd0;
        t32 = 32'd0;
        g33 = 32'd0;
        r3 = 32'd0;
        l33_t = 32'd0;

        if (diag_gt_zero(g00)) begin
            r0   = fp32_div_bits(FP_ONE, fp32_sqrt_bits(g00));
            l00_t = fp32_mul_bits(g00, r0);
            l10_t = fp32_mul_bits(g10, r0);
            l20_t = fp32_mul_bits(g20, r0);
            l30_t = fp32_mul_bits(g30, r0);

            g11 = fp32_sub_bits(in_a11, fp32_mul_bits(l10_t, l10_t));
            g21 = fp32_sub_bits(in_a21, fp32_mul_bits(l10_t, l20_t));
            g31 = fp32_sub_bits(in_a31, fp32_mul_bits(l10_t, l30_t));

            if (diag_gt_zero(g11)) begin
                r1   = fp32_div_bits(FP_ONE, fp32_sqrt_bits(g11));
                l11_t = fp32_mul_bits(g11, r1);
                l21_t = fp32_mul_bits(g21, r1);
                l31_t = fp32_mul_bits(g31, r1);

                t20 = fp32_sub_bits(in_a22, fp32_mul_bits(l20_t, l20_t));
                g22 = fp32_sub_bits(t20, fp32_mul_bits(l21_t, l21_t));
                t30 = fp32_sub_bits(in_a32, fp32_mul_bits(l20_t, l30_t));
                g32 = fp32_sub_bits(t30, fp32_mul_bits(l21_t, l31_t));

                if (diag_gt_zero(g22)) begin
                    r2   = fp32_div_bits(FP_ONE, fp32_sqrt_bits(g22));
                    l22_t = fp32_mul_bits(g22, r2);
                    l32_t = fp32_mul_bits(g32, r2);

                    t31 = fp32_sub_bits(in_a33, fp32_mul_bits(l30_t, l30_t));
                    t32 = fp32_sub_bits(t31, fp32_mul_bits(l31_t, l31_t));
                    g33 = fp32_sub_bits(t32, fp32_mul_bits(l32_t, l32_t));

                    if (diag_gt_zero(g33)) begin
                        r3   = fp32_div_bits(FP_ONE, fp32_sqrt_bits(g33));
                        l33_t = fp32_mul_bits(g33, r3);

                        calc_l00 = l00_t;
                        calc_l01 = 32'd0;
                        calc_l02 = 32'd0;
                        calc_l03 = 32'd0;
                        calc_l10 = l10_t;
                        calc_l11 = l11_t;
                        calc_l12 = 32'd0;
                        calc_l13 = 32'd0;
                        calc_l20 = l20_t;
                        calc_l21 = l21_t;
                        calc_l22 = l22_t;
                        calc_l23 = 32'd0;
                        calc_l30 = l30_t;
                        calc_l31 = l31_t;
                        calc_l32 = l32_t;
                        calc_l33 = l33_t;
                        calc_status = 2'd0;
                    end
                end
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            out_l00 <= 32'd0;
            out_l01 <= 32'd0;
            out_l02 <= 32'd0;
            out_l03 <= 32'd0;
            out_l10 <= 32'd0;
            out_l11 <= 32'd0;
            out_l12 <= 32'd0;
            out_l13 <= 32'd0;
            out_l20 <= 32'd0;
            out_l21 <= 32'd0;
            out_l22 <= 32'd0;
            out_l23 <= 32'd0;
            out_l30 <= 32'd0;
            out_l31 <= 32'd0;
            out_l32 <= 32'd0;
            out_l33 <= 32'd0;
            out_status <= 2'd0;
        end else if (input_accepted) begin
            busy <= 1'b1;
            out_l00 <= calc_l00;
            out_l01 <= calc_l01;
            out_l02 <= calc_l02;
            out_l03 <= calc_l03;
            out_l10 <= calc_l10;
            out_l11 <= calc_l11;
            out_l12 <= calc_l12;
            out_l13 <= calc_l13;
            out_l20 <= calc_l20;
            out_l21 <= calc_l21;
            out_l22 <= calc_l22;
            out_l23 <= calc_l23;
            out_l30 <= calc_l30;
            out_l31 <= calc_l31;
            out_l32 <= calc_l32;
            out_l33 <= calc_l33;
            out_status <= calc_status;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
