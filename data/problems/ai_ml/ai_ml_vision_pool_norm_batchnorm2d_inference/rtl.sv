/* verilator lint_off WIDTH */
/* verilator lint_off WIDTHEXPAND */
/* verilator lint_off WIDTHTRUNC */
/* verilator lint_off SELRANGE */
module batchnorm2d_inference (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x0,
    input  logic [31:0] in_x1,
    input  logic [31:0] in_x2,
    input  logic [31:0] in_x3,
    input  logic [31:0] in_weight0,
    input  logic [31:0] in_weight1,
    input  logic [31:0] in_weight2,
    input  logic [31:0] in_weight3,
    input  logic [31:0] in_bias0,
    input  logic [31:0] in_bias1,
    input  logic [31:0] in_bias2,
    input  logic [31:0] in_bias3,
    input  logic [31:0] in_running_mean0,
    input  logic [31:0] in_running_mean1,
    input  logic [31:0] in_running_mean2,
    input  logic [31:0] in_running_mean3,
    input  logic [31:0] in_running_var0,
    input  logic [31:0] in_running_var1,
    input  logic [31:0] in_running_var2,
    input  logic [31:0] in_running_var3,
    input  logic [31:0] in_eps,

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

    localparam logic [31:0] FP32_ONE      = 32'h3F800000;
    localparam logic [31:0] FP32_QNAN     = 32'h7FC00000;
    localparam logic [31:0] FP32_POS_INF  = 32'h7F800000;
    localparam logic [31:0] FP32_POS_ZERO = 32'h00000000;

    logic busy;
    wire input_accepted;
    wire output_consumed;

    logic [31:0] calc_y0, calc_y1, calc_y2, calc_y3;

    logic [31:0] delta0, delta1, delta2, delta3;
    logic [31:0] var_eps0, var_eps1, var_eps2, var_eps3;
    logic [31:0] sqrt0, sqrt1, sqrt2, sqrt3;
    logic [31:0] inv_std0, inv_std1, inv_std2, inv_std3;
    logic [31:0] mul00, mul01, mul02, mul03;
    logic [31:0] scaled0, scaled1, scaled2, scaled3;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        delta0   = fp32_add_bits(in_x0, {~in_running_mean0[31], in_running_mean0[30:0]});
        var_eps0 = fp32_add_bits(in_running_var0, in_eps);
        sqrt0    = fp32_sqrt_bits(var_eps0);
        inv_std0 = fp32_div_bits(FP32_ONE, sqrt0);
        mul00    = fp32_mul_bits(delta0, inv_std0);
        scaled0  = fp32_mul_bits(mul00, in_weight0);
        calc_y0  = fp32_add_bits(scaled0, in_bias0);

        delta1   = fp32_add_bits(in_x1, {~in_running_mean1[31], in_running_mean1[30:0]});
        var_eps1 = fp32_add_bits(in_running_var1, in_eps);
        sqrt1    = fp32_sqrt_bits(var_eps1);
        inv_std1 = fp32_div_bits(FP32_ONE, sqrt1);
        mul01    = fp32_mul_bits(delta1, inv_std1);
        scaled1  = fp32_mul_bits(mul01, in_weight1);
        calc_y1  = fp32_add_bits(scaled1, in_bias1);

        delta2   = fp32_add_bits(in_x2, {~in_running_mean2[31], in_running_mean2[30:0]});
        var_eps2 = fp32_add_bits(in_running_var2, in_eps);
        sqrt2    = fp32_sqrt_bits(var_eps2);
        inv_std2 = fp32_div_bits(FP32_ONE, sqrt2);
        mul02    = fp32_mul_bits(delta2, inv_std2);
        scaled2  = fp32_mul_bits(mul02, in_weight2);
        calc_y2  = fp32_add_bits(scaled2, in_bias2);

        delta3   = fp32_add_bits(in_x3, {~in_running_mean3[31], in_running_mean3[30:0]});
        var_eps3 = fp32_add_bits(in_running_var3, in_eps);
        sqrt3    = fp32_sqrt_bits(var_eps3);
        inv_std3 = fp32_div_bits(FP32_ONE, sqrt3);
        mul03    = fp32_mul_bits(delta3, inv_std3);
        scaled3  = fp32_mul_bits(mul03, in_weight3);
        calc_y3  = fp32_add_bits(scaled3, in_bias3);
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy   <= 1'b0;
            out_y0 <= 32'h00000000;
            out_y1 <= 32'h00000000;
            out_y2 <= 32'h00000000;
            out_y3 <= 32'h00000000;
        end else if (input_accepted) begin
            out_y0 <= calc_y0;
            out_y1 <= calc_y1;
            out_y2 <= calc_y2;
            out_y3 <= calc_y3;
            busy   <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

    localparam logic [31:0] FP32_CANON_QNAN = 32'h7FC00000;

endmodule
