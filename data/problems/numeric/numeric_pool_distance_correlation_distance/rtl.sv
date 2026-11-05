module CorrelationDistanceF32Vec4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a0,
    input  logic [31:0] in_a1,
    input  logic [31:0] in_a2,
    input  logic [31:0] in_a3,
    input  logic [31:0] in_b0,
    input  logic [31:0] in_b1,
    input  logic [31:0] in_b2,
    input  logic [31:0] in_b3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_distance
);
    localparam logic [31:0] FP32_ONE  = 32'h3F800000;
    localparam logic [31:0] FP32_FOUR = 32'h40800000;

    logic busy;
    logic [31:0] out_distance_next;

    wire input_accepted;
    wire output_consumed;

    logic [31:0] sum_a_01;
    logic [31:0] sum_a_012;
    logic [31:0] sum_a;
    logic [31:0] sum_b_01;
    logic [31:0] sum_b_012;
    logic [31:0] sum_b;
    logic [31:0] mean_a;
    logic [31:0] mean_b;
    logic [31:0] ca0, ca1, ca2, ca3;
    logic [31:0] cb0, cb1, cb2, cb3;
    logic [31:0] ca0_sq, ca1_sq, ca2_sq, ca3_sq;
    logic [31:0] cb0_sq, cb1_sq, cb2_sq, cb3_sq;
    logic [31:0] dot0, dot1, dot2, dot3;
    logic [31:0] pwra_01, pwra_012, pwra;
    logic [31:0] pwrb_01, pwrb_012, pwrb;
    logic [31:0] dot_01, dot_012, dot;
    logic [31:0] dot_avg;
    logic [31:0] pwra_avg;
    logic [31:0] pwrb_avg;
    logic [31:0] power_product;
    logic [31:0] denominator;
    logic [31:0] correlation;

    assign input_accepted = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready = ~busy | output_consumed;
    assign out_valid = busy;

    fp32_add u_sum_a_01  (.a(in_a0),     .b(in_a1),      .y(sum_a_01));
    fp32_add u_sum_a_012 (.a(sum_a_01),  .b(in_a2),      .y(sum_a_012));
    fp32_add u_sum_a     (.a(sum_a_012), .b(in_a3),      .y(sum_a));

    fp32_add u_sum_b_01  (.a(in_b0),     .b(in_b1),      .y(sum_b_01));
    fp32_add u_sum_b_012 (.a(sum_b_01),  .b(in_b2),      .y(sum_b_012));
    fp32_add u_sum_b     (.a(sum_b_012), .b(in_b3),      .y(sum_b));

    fp32_div u_mean_a (.a(sum_a), .b(FP32_FOUR), .y(mean_a));
    fp32_div u_mean_b (.a(sum_b), .b(FP32_FOUR), .y(mean_b));

    fp32_sub u_ca0 (.a(in_a0), .b(mean_a), .y(ca0));
    fp32_sub u_ca1 (.a(in_a1), .b(mean_a), .y(ca1));
    fp32_sub u_ca2 (.a(in_a2), .b(mean_a), .y(ca2));
    fp32_sub u_ca3 (.a(in_a3), .b(mean_a), .y(ca3));

    fp32_sub u_cb0 (.a(in_b0), .b(mean_b), .y(cb0));
    fp32_sub u_cb1 (.a(in_b1), .b(mean_b), .y(cb1));
    fp32_sub u_cb2 (.a(in_b2), .b(mean_b), .y(cb2));
    fp32_sub u_cb3 (.a(in_b3), .b(mean_b), .y(cb3));

    fp32_mul u_ca0_sq (.a(ca0), .b(ca0), .y(ca0_sq));
    fp32_mul u_ca1_sq (.a(ca1), .b(ca1), .y(ca1_sq));
    fp32_mul u_ca2_sq (.a(ca2), .b(ca2), .y(ca2_sq));
    fp32_mul u_ca3_sq (.a(ca3), .b(ca3), .y(ca3_sq));

    fp32_mul u_cb0_sq (.a(cb0), .b(cb0), .y(cb0_sq));
    fp32_mul u_cb1_sq (.a(cb1), .b(cb1), .y(cb1_sq));
    fp32_mul u_cb2_sq (.a(cb2), .b(cb2), .y(cb2_sq));
    fp32_mul u_cb3_sq (.a(cb3), .b(cb3), .y(cb3_sq));

    fp32_mul u_dot0 (.a(ca0), .b(cb0), .y(dot0));
    fp32_mul u_dot1 (.a(ca1), .b(cb1), .y(dot1));
    fp32_mul u_dot2 (.a(ca2), .b(cb2), .y(dot2));
    fp32_mul u_dot3 (.a(ca3), .b(cb3), .y(dot3));

    fp32_add u_pwra_01  (.a(ca0_sq),   .b(ca1_sq),   .y(pwra_01));
    fp32_add u_pwra_012 (.a(pwra_01),  .b(ca2_sq),   .y(pwra_012));
    fp32_add u_pwra     (.a(pwra_012), .b(ca3_sq),   .y(pwra));

    fp32_add u_pwrb_01  (.a(cb0_sq),   .b(cb1_sq),   .y(pwrb_01));
    fp32_add u_pwrb_012 (.a(pwrb_01),  .b(cb2_sq),   .y(pwrb_012));
    fp32_add u_pwrb     (.a(pwrb_012), .b(cb3_sq),   .y(pwrb));

    fp32_add u_dot_01   (.a(dot0),     .b(dot1),     .y(dot_01));
    fp32_add u_dot_012  (.a(dot_01),   .b(dot2),     .y(dot_012));
    fp32_add u_dot      (.a(dot_012),  .b(dot3),     .y(dot));

    fp32_div u_dot_avg  (.a(dot),  .b(FP32_FOUR), .y(dot_avg));
    fp32_div u_pwra_avg (.a(pwra), .b(FP32_FOUR), .y(pwra_avg));
    fp32_div u_pwrb_avg (.a(pwrb), .b(FP32_FOUR), .y(pwrb_avg));

    fp32_mul  u_power_product (.a(pwra_avg), .b(pwrb_avg),    .y(power_product));
    fp32_sqrt u_denominator   (.a(power_product),             .y(denominator));
    fp32_div  u_correlation   (.a(dot_avg),  .b(denominator), .y(correlation));
    fp32_sub  u_distance      (.a(FP32_ONE), .b(correlation), .y(out_distance_next));

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            out_distance <= 32'd0;
        end else if (input_accepted) begin
            busy <= 1'b1;
            out_distance <= out_distance_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end
endmodule

module fp32_add(
    input  logic [31:0] a,
    input  logic [31:0] b,
    output logic [31:0] y
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
assign y = fp32_add_bits(a, b);
endmodule

module fp32_sub(
    input  logic [31:0] a,
    input  logic [31:0] b,
    output logic [31:0] y
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
assign y = fp32_add_bits(a, b ^ 32'h80000000);
endmodule

module fp32_mul(
    input  logic [31:0] a,
    input  logic [31:0] b,
    output logic [31:0] y
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
assign y = fp32_mul_bits(a, b);
endmodule

module fp32_div(
    input  logic [31:0] a,
    input  logic [31:0] b,
    output logic [31:0] y
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
assign y = fp32_div_bits(a, b);
endmodule

module fp32_sqrt(
    input  logic [31:0] a,
    output logic [31:0] y
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
assign y = fp32_sqrt_bits(a);
endmodule
