module ai_ml_tx_norm_ln_bert_layernorm (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x0,
    input  logic [31:0] in_x1,
    input  logic [31:0] in_x2,
    input  logic [31:0] in_x3,
    input  logic [31:0] in_gamma0,
    input  logic [31:0] in_gamma1,
    input  logic [31:0] in_gamma2,
    input  logic [31:0] in_gamma3,
    input  logic [31:0] in_beta0,
    input  logic [31:0] in_beta1,
    input  logic [31:0] in_beta2,
    input  logic [31:0] in_beta3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3
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

    localparam logic [31:0] FOUR          = 32'h40800000;
    localparam logic [31:0] EPSILON       = 32'h3727C5AC;
    localparam logic [31:0] CANONICAL_NAN = 32'h7FC00000;
    localparam logic [31:0] POS_INF       = 32'h7F800000;

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    logic [31:0] calc_sum01, calc_sum23, calc_sum_w, calc_mean;
    logic [31:0] calc_d0, calc_d1, calc_d2, calc_d3;
    logic [31:0] calc_s0, calc_s1, calc_s2, calc_s3;
    logic [31:0] calc_vsum01, calc_vsum23, calc_vsum, calc_variance;
    logic [31:0] calc_variance_eps, calc_stddev;
    logic [31:0] calc_n0, calc_n1, calc_n2, calc_n3;
    logic [31:0] calc_m0, calc_m1, calc_m2, calc_m3;
    logic [31:0] calc_y0, calc_y1, calc_y2, calc_y3;

    always_comb begin
        calc_sum01 = fp32_add_bits(in_x0, in_x1);
        calc_sum23 = fp32_add_bits(in_x2, in_x3);
        calc_sum_w = fp32_add_bits(calc_sum01, calc_sum23);
        calc_mean  = fp32_div_bits(calc_sum_w, FOUR);

        calc_d0 = fp32_sub_bits(in_x0, calc_mean);
        calc_d1 = fp32_sub_bits(in_x1, calc_mean);
        calc_d2 = fp32_sub_bits(in_x2, calc_mean);
        calc_d3 = fp32_sub_bits(in_x3, calc_mean);

        calc_s0 = fp32_mul_bits(calc_d0, calc_d0);
        calc_s1 = fp32_mul_bits(calc_d1, calc_d1);
        calc_s2 = fp32_mul_bits(calc_d2, calc_d2);
        calc_s3 = fp32_mul_bits(calc_d3, calc_d3);

        calc_vsum01   = fp32_add_bits(calc_s0, calc_s1);
        calc_vsum23   = fp32_add_bits(calc_s2, calc_s3);
        calc_vsum     = fp32_add_bits(calc_vsum01, calc_vsum23);
        calc_variance = fp32_div_bits(calc_vsum, FOUR);

        calc_variance_eps = fp32_add_bits(calc_variance, EPSILON);
        calc_stddev       = fp32_sqrt_bits(calc_variance_eps);

        calc_n0 = fp32_div_bits(calc_d0, calc_stddev);
        calc_n1 = fp32_div_bits(calc_d1, calc_stddev);
        calc_n2 = fp32_div_bits(calc_d2, calc_stddev);
        calc_n3 = fp32_div_bits(calc_d3, calc_stddev);

        calc_m0 = fp32_mul_bits(calc_n0, in_gamma0);
        calc_m1 = fp32_mul_bits(calc_n1, in_gamma1);
        calc_m2 = fp32_mul_bits(calc_n2, in_gamma2);
        calc_m3 = fp32_mul_bits(calc_n3, in_gamma3);

        calc_y0 = fp32_add_bits(calc_m0, in_beta0);
        calc_y1 = fp32_add_bits(calc_m1, in_beta1);
        calc_y2 = fp32_add_bits(calc_m2, in_beta2);
        calc_y3 = fp32_add_bits(calc_m3, in_beta3);
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy   <= 1'b0;
            out_y0 <= 32'd0;
            out_y1 <= 32'd0;
            out_y2 <= 32'd0;
            out_y3 <= 32'd0;
        end else if (input_accepted) begin
            busy   <= 1'b1;
            out_y0 <= calc_y0;
            out_y1 <= calc_y1;
            out_y2 <= calc_y2;
            out_y3 <= calc_y3;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
